"""Correos de TenderoBot: cada uno en HTML (con el logo y los colores de la marca) y en texto plano.

Las plantillas HTML están en app/web/templates/email/. El texto plano va como alternativa para
los programas de correo que no muestran HTML.
"""

from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.phones import format_phone

_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).parent / "web" / "templates")),
    autoescape=select_autoescape(["html"]),
)


@dataclass(frozen=True)
class Email:
    subject: str
    text: str
    html: str


def _render(template: str, subject: str, **context) -> str:
    return _env.get_template(f"email/{template}.html").render(subject=subject, **context)


def code_email(code: str, minutes: int) -> Email:
    subject = f"Tu código de TenderoBot: {code}"
    return Email(
        subject,
        f"Tu código para registrarte en TenderoBot es:\n\n    {code}\n\n"
        f"Escríbelo en la página. Vence en {minutes} minutos.\n\n"
        "Si no pediste este código, ignora este correo.\n\n— TenderoBot",
        _render(
            "code", subject, code=code, minutes=minutes, name=None, title="Confirma tu correo",
            preheader=f"Tu código es {code}. Vence en {minutes} minutos.",
        ),
    )


def _open_bot_text(link: str) -> str:
    return f"Ábrelo aquí: {link}" if link else "Búscalo en Telegram como TenderoBot."


def reminder_email(owner: str, store_name: str, phone: str, link: str) -> Email:
    phone = format_phone(phone)
    subject = "Abre TenderoBot para saber cuándo te aprueban"
    return Email(
        subject,
        f"Hola, {owner}.\n\n"
        f"Recibimos tu solicitud para usar TenderoBot en {store_name}. Tu distribuidor la está revisando.\n\n"
        "Para que te avisemos por Telegram apenas te respondan, abre el bot y toca "
        f"\"📱 Compartir mi número\" (usa el celular {phone}).\n{_open_bot_text(link)}\n\n— TenderoBot",
        _render(
            "reminder", subject, name=owner, store_name=store_name, phone=phone, link=link,
            title="Falta un paso para que te avisemos",
            preheader="Abre el bot en Telegram para saber apenas te respondan.",
        ),
    )


def approved_email(owner: str, store_name: str, distributor: str, phone: str, link: str) -> Email:
    phone = format_phone(phone)
    subject = "✅ Tu tienda fue aprobada en TenderoBot"
    return Email(
        subject,
        f"Hola, {owner}.\n\n"
        f"{distributor} aprobó tu tienda {store_name}. Ya puedes usar TenderoBot.\n\n"
        "Abre el bot en Telegram y toca \"📱 Compartir mi número\" "
        f"(usa el celular {phone}).\n{_open_bot_text(link)}\n\n— TenderoBot",
        _render(
            "approved", subject, name=owner, store_name=store_name, distributor=distributor, phone=phone,
            link=link, title="¡Tu tienda fue aprobada! 🎉",
            preheader=f"{distributor} aprobó {store_name}. Ya puedes empezar.",
        ),
    )


def rejected_email(owner: str, store_name: str, reason: str, page: str) -> Email:
    subject = "Tu solicitud en TenderoBot no fue aprobada"
    retry = f"\n\nSi lo puedes corregir, envía una solicitud nueva en {page}" if page else ""
    return Email(
        subject,
        f"Hola, {owner}.\n\nTu solicitud para {store_name} no fue aprobada.\nMotivo: {reason}{retry}\n\n— TenderoBot",
        _render(
            "rejected", subject, name=owner, store_name=store_name, reason=reason, page=page,
            title="Tu solicitud no fue aprobada", preheader=f"Motivo: {reason}",
        ),
    )
