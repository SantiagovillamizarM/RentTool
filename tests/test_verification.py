"""Registro con correo: código de 6 dígitos, vencimiento, intentos y límites de envío."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.web import verification
from app.web.verification import VerificationError, check_code, hash_code, send_code

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
EMAIL = "rosa@gmail.com"


class CodeSession:
    """Sesión falsa: `scalar` devuelve las respuestas en orden; guarda lo agregado y los commits."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.added = []
        self.commits = 0

    async def scalar(self, statement):
        return self.answers.pop(0)

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.commits += 1


def code_row(code="123456", minutes_left=5, attempts=0):
    return SimpleNamespace(
        code_hash=hash_code(EMAIL, code), expires_at=NOW + timedelta(minutes=minutes_left), attempts=attempts, used_at=None
    )


def test_codes_are_six_digits_and_hashed_per_email():
    code = verification.new_code()
    assert len(code) == 6 and code.isdigit()
    assert hash_code(EMAIL, "123456") != hash_code("otro@gmail.com", "123456")
    assert "123456" not in hash_code(EMAIL, "123456")


@pytest.mark.asyncio
async def test_right_code_is_used_once():
    row = code_row()
    await check_code(CodeSession(row), EMAIL, " 123 456 ", NOW)
    assert row.used_at == NOW


@pytest.mark.asyncio
async def test_wrong_code_counts_attempts_until_blocked():
    row = code_row(attempts=3)
    with pytest.raises(VerificationError, match="Te quedan 1 intento\\."):
        await check_code(CodeSession(row), EMAIL, "000000", NOW)
    with pytest.raises(VerificationError, match="muchas veces"):
        await check_code(CodeSession(row), EMAIL, "000000", NOW)
    # Bloqueado: ni el código correcto sirve; hay que pedir otro.
    with pytest.raises(VerificationError, match="muchas veces"):
        await check_code(CodeSession(row), EMAIL, "123456", NOW)
    assert row.used_at is None


@pytest.mark.asyncio
async def test_expired_or_missing_code():
    with pytest.raises(VerificationError, match="venció"):
        await check_code(CodeSession(code_row(minutes_left=-1)), EMAIL, "123456", NOW)
    with pytest.raises(VerificationError, match="venció"):
        await check_code(CodeSession(None), EMAIL, "123456", NOW)


@pytest.mark.asyncio
async def test_send_code_waits_a_minute_and_caps_per_hour(monkeypatch):
    monkeypatch.setattr(verification, "email_enabled", lambda: False)
    with pytest.raises(VerificationError, match="Espera un minuto"):
        await send_code(CodeSession(NOW - timedelta(seconds=20)), EMAIL, NOW)
    with pytest.raises(VerificationError, match="una hora"):
        await send_code(CodeSession(NOW - timedelta(minutes=5), 5), EMAIL, NOW)
    session = CodeSession(NOW - timedelta(minutes=5), 1)
    await send_code(session, EMAIL, NOW)
    [row] = session.added
    assert row.email == EMAIL and row.expires_at == NOW + verification.CODE_TTL


@pytest.mark.asyncio
async def test_send_code_reports_mail_failure(monkeypatch):
    monkeypatch.setattr(verification, "email_enabled", lambda: True)

    async def fail(to, email):
        return False

    monkeypatch.setattr(verification, "send_email", fail)
    with pytest.raises(VerificationError, match="No pudimos enviarte"):
        await send_code(CodeSession(None, 0), EMAIL, NOW)


def test_code_email_shows_the_code():
    email = verification.code_email("654321")
    assert "654321" in email.subject and "654321" in email.text
    assert "654 321" in email.html
