import json
from typing import Any

from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError

from app.ai.prompts import (
    SALES_RESPONSE_SCHEMA,
    STOCK_RESPONSE_SCHEMA,
    sales_prompt,
    stock_prompt,
)
from app.config import get_settings
from app.schemas import SaleLine, StockLine

_client: genai.Client | None = None


class GeminiExtractError(Exception):
    def __init__(self, message: str = "No te entendí, intenta de nuevo o escríbelo") -> None:
        super().__init__(message)
        self.user_message = message


class SalesExtraction(BaseModel):
    lines: list[SaleLine]


class StockExtraction(BaseModel):
    lines: list[StockLine]


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        settings = get_settings()
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _normalize_sale_lines(lines: list[SaleLine]) -> list[SaleLine]:
    return [
        line.model_copy(update={"sku": _blank_to_none(line.sku), "product_text": line.product_text.strip()})
        for line in lines
    ]


def _normalize_stock_lines(lines: list[StockLine]) -> list[StockLine]:
    return [line.model_copy(update={"sku": _blank_to_none(line.sku)}) for line in lines]


async def _generate_json(contents: Any, response_schema: dict) -> dict:
    settings = get_settings()
    response = await _get_client().aio.models.generate_content(
        model=settings.gemini_model,
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=response_schema,
            temperature=0,
        ),
    )
    text = (response.text or "").strip()
    if not text:
        raise GeminiExtractError()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GeminiExtractError() from exc
    if not isinstance(data, dict):
        raise GeminiExtractError()
    return data


async def _extract_with_retry(contents: Any, response_schema: dict, payload_model: type[BaseModel]) -> BaseModel:
    last_error: BaseException | None = None
    for _ in range(2):
        try:
            data = await _generate_json(contents, response_schema)
            return payload_model.model_validate(data)
        except (GeminiExtractError, ValidationError) as exc:
            last_error = exc
        except Exception as exc:
            last_error = exc
    raise GeminiExtractError() from last_error


async def extract_sales(
    products: list[dict],
    transcript: str | None = None,
    audio_bytes: bytes | None = None,
    audio_mime_type: str | None = None,
) -> list[SaleLine]:
    text = (transcript or "").strip()
    if not text and not audio_bytes:
        raise ValueError("transcript or audio_bytes is required")
    if audio_bytes and not audio_mime_type:
        raise ValueError("audio_mime_type is required when audio_bytes is set")

    parts: list[types.Part] = []
    if audio_bytes:
        parts.append(types.Part.from_bytes(data=audio_bytes, mime_type=audio_mime_type))
    parts.append(types.Part.from_text(text=sales_prompt(text, products)))

    payload = await _extract_with_retry(parts, SALES_RESPONSE_SCHEMA, SalesExtraction)
    return _normalize_sale_lines(payload.lines)


async def extract_stock(
    products: list[dict],
    image_bytes: bytes,
    image_mime_type: str,
    note: str = "",
) -> list[StockLine]:
    if not image_bytes:
        raise ValueError("image_bytes is required")
    if not image_mime_type:
        raise ValueError("image_mime_type is required")

    parts = [
        types.Part.from_bytes(data=image_bytes, mime_type=image_mime_type),
        types.Part.from_text(text=stock_prompt(products, note=note)),
    ]
    payload = await _extract_with_retry(parts, STOCK_RESPONSE_SCHEMA, StockExtraction)
    return _normalize_stock_lines(payload.lines)
