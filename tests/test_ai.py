import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from app.ai.gemini_client import GeminiExtractError, extract_sales, extract_stock
from app.ai.matching import MATCH_THRESHOLD, match_sale_lines, match_stock_lines, resolve_sku
from app.ai.prompts import sales_prompt, stock_prompt
from app.schemas import SaleLine, StockLine

CATALOG = [
    {
        "sku": "GAS-COCA-1L",
        "name": "Coca-Cola 1 litro",
        "aliases": "coca litro,coca cola litro,coca 1l,gaseosa coca litro",
    },
    {
        "sku": "PAN-TAJA-UND",
        "name": "Pan tajado",
        "aliases": "pan,pan tajado,pan de caja,tajado",
    },
]


def fake_response(payload: dict | str) -> SimpleNamespace:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(text=text)


def patch_generate(side_effect):
    client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=AsyncMock(side_effect=side_effect))))
    return patch("app.ai.gemini_client._get_client", return_value=client)


# --- Matching ----------------------------------------------------------------


def test_resolve_sku_keeps_catalog_sku():
    assert resolve_sku("GAS-COCA-1L", "cualquier texto", CATALOG) == "GAS-COCA-1L"
    assert resolve_sku("gas-coca-1l", "", CATALOG) == "GAS-COCA-1L"


def test_resolve_sku_fuzzy_on_name_and_aliases():
    assert resolve_sku(None, "coca cola litro", CATALOG) == "GAS-COCA-1L"
    assert resolve_sku("SKU-INVENTADO", "pan tajado", CATALOG) == "PAN-TAJA-UND"


def test_resolve_sku_below_threshold_is_unrecognized():
    assert MATCH_THRESHOLD == 80
    assert resolve_sku(None, "xyzqwerty123", CATALOG) is None
    assert resolve_sku("NO-EXISTE", "xyzqwerty123", CATALOG) is None


def test_match_sale_lines_sets_null_when_unrecognized():
    lines = [
        SaleLine(sku="GAS-COCA-1L", product_text="coca litro", quantity=2),
        SaleLine(sku=None, product_text="pan", quantity=1),
        SaleLine(sku=None, product_text="producto raro", quantity=3),
    ]
    matched = match_sale_lines(lines, CATALOG)
    assert matched[0].sku == "GAS-COCA-1L"
    assert matched[1].sku == "PAN-TAJA-UND"
    assert matched[2].sku is None


def test_match_stock_lines_only_uses_sku():
    lines = [
        StockLine(sku="PAN-TAJA-UND", quantity=12, confidence=0.9),
        StockLine(sku="NO-EXISTE", quantity=4, confidence=0.4),
    ]
    matched = match_stock_lines(lines, CATALOG)
    assert matched[0].sku == "PAN-TAJA-UND"
    assert matched[1].sku is None


# --- Prompts -----------------------------------------------------------------


def test_prompts_do_not_ask_gemini_to_calculate():
    sales = sales_prompt("vendí 2 coca litro", CATALOG)
    stock = stock_prompt(CATALOG)
    assert "No calcules" in sales
    assert "No calcules" in stock
    assert "GAS-COCA-1L" in sales
    assert "coca litro" in sales


# --- Schemas -----------------------------------------------------------------


def test_sale_line_rejects_non_positive_quantity():
    with pytest.raises(ValidationError):
        SaleLine(sku=None, product_text="coca", quantity=0)


def test_stock_line_rejects_confidence_out_of_range():
    with pytest.raises(ValidationError):
        StockLine(sku=None, quantity=1, confidence=1.5)


# --- Gemini simulado ---------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_sales_validates_json_from_gemini():
    payload = {
        "lines": [
            {"sku": "GAS-COCA-1L", "product_text": "coca cola litro", "quantity": 2},
            {"sku": None, "product_text": "un pan", "quantity": 1},
        ]
    }
    with patch_generate([fake_response(payload)]) as mocked:
        lines = await extract_sales(CATALOG, transcript="vendí 2 coca cola litro y un pan")
    mocked.return_value.aio.models.generate_content.assert_awaited_once()
    assert lines == [
        SaleLine(sku="GAS-COCA-1L", product_text="coca cola litro", quantity=2),
        SaleLine(sku=None, product_text="un pan", quantity=1),
    ]


@pytest.mark.asyncio
async def test_extract_stock_validates_json_from_gemini():
    payload = {"lines": [{"sku": "GAS-COCA-1L", "quantity": 8, "confidence": 0.7}]}
    with patch_generate([fake_response(payload)]):
        lines = await extract_stock(CATALOG, b"fake-image", "image/jpeg")
    assert lines == [StockLine(sku="GAS-COCA-1L", quantity=8, confidence=0.7)]


@pytest.mark.asyncio
async def test_extract_sales_retries_invalid_json_then_succeeds():
    good = {"lines": [{"sku": None, "product_text": "coca litro", "quantity": 2}]}
    with patch_generate([fake_response("{no-json"), fake_response(good)]) as mocked:
        lines = await extract_sales(CATALOG, transcript="vendí 2 coca litro")
    assert mocked.return_value.aio.models.generate_content.await_count == 2
    assert lines[0].quantity == 2


@pytest.mark.asyncio
async def test_extract_sales_retries_once_then_raises_user_error():
    with patch_generate([TimeoutError("timeout"), TimeoutError("timeout")]) as mocked:
        with pytest.raises(GeminiExtractError) as exc:
            await extract_sales(CATALOG, transcript="vendí 2 coca")
    assert mocked.return_value.aio.models.generate_content.await_count == 2
    assert str(exc.value) == "No te entendí, intenta de nuevo o escríbelo"


@pytest.mark.asyncio
async def test_extract_stock_retries_quota_then_fails():
    with patch_generate([RuntimeError("quota"), fake_response("not-json")]):
        with pytest.raises(GeminiExtractError) as exc:
            await extract_stock(CATALOG, b"foto", "image/jpeg")
    assert exc.value.user_message == "No te entendí, intenta de nuevo o escríbelo"


@pytest.mark.asyncio
async def test_extract_sales_rejects_invalid_quantity_after_retry():
    bad = {"lines": [{"sku": None, "product_text": "coca", "quantity": 0}]}
    with patch_generate([fake_response(bad), fake_response(bad)]):
        with pytest.raises(GeminiExtractError):
            await extract_sales(CATALOG, transcript="vendí cero coca")
