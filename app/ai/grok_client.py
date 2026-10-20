"""Cliente de Grok (API compatible con OpenAI del reto).

Grok solo extrae datos: voz, texto o foto → JSON. Ningún cálculo se hace aquí.
- Voz: /stt la transcribe a texto y luego se extrae como si fuera texto.
- Texto y foto: /chat/completions con salida JSON según un esquema.
"""

import base64
import json
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from app.ai.prompts import (
    ORDER_CHANGES_RESPONSE_SCHEMA,
    SALES_RESPONSE_SCHEMA,
    STOCK_RESPONSE_SCHEMA,
    order_changes_prompt,
    sales_prompt,
    stock_prompt,
)
from app.config import get_settings
from app.schemas import OrderChange, SaleLine, StockLine

REQUEST_TIMEOUT_SECONDS = 60.0

_client: httpx.AsyncClient | None = None


class ExtractError(Exception):
    def __init__(self, message: str = "No te entendí, intenta de nuevo o escríbelo") -> None:
        super().__init__(message)
        self.user_message = message


class SalesExtraction(BaseModel):
    lines: list[SaleLine]


class StockExtraction(BaseModel):
    lines: list[StockLine]


class OrderChangesExtraction(BaseModel):
    changes: list[OrderChange]


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        settings = get_settings()
        _client = httpx.AsyncClient(
            base_url=settings.grok_base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {settings.grok_api_key}"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
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


async def _chat_completion(content: Any, response_schema: dict, schema_name: str) -> str:
    """Una llamada a Grok. Devuelve el texto de la respuesta (debería ser JSON)."""
    settings = get_settings()
    response = await _get_client().post(
        "/chat/completions",
        json={
            "model": settings.grok_model,
            "temperature": 0,
            "messages": [{"role": "user", "content": content}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "schema": response_schema},
            },
        },
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"].get("content") or ""


async def transcribe_audio(audio_bytes: bytes, mime_type: str) -> str:
    """Voz → texto con el endpoint /stt. El audio no se guarda en ningún lado."""
    extension = "ogg" if "ogg" in mime_type else mime_type.rsplit("/", 1)[-1]
    response = await _get_client().post(
        "/stt",
        files={"file": (f"voice.{extension}", audio_bytes, mime_type)},
        data={"language": "es"},
    )
    response.raise_for_status()
    return str(response.json().get("text") or "").strip()


async def _generate_json(content: Any, response_schema: dict, schema_name: str) -> dict:
    text = (await _chat_completion(content, response_schema, schema_name)).strip()
    if not text:
        raise ExtractError()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExtractError() from exc
    if not isinstance(data, dict):
        raise ExtractError()
    return data


async def _extract_with_retry(
    content: Any, response_schema: dict, schema_name: str, payload_model: type[BaseModel]
) -> BaseModel:
    last_error: BaseException | None = None
    for _ in range(2):
        try:
            data = await _generate_json(content, response_schema, schema_name)
            return payload_model.model_validate(data)
        except (ExtractError, ValidationError) as exc:
            last_error = exc
        except Exception as exc:
            last_error = exc
    raise ExtractError() from last_error


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

    if audio_bytes:
        try:
            text = await transcribe_audio(audio_bytes, audio_mime_type or "")
        except Exception as exc:
            raise ExtractError() from exc
        if not text:
            raise ExtractError()

    payload = await _extract_with_retry(sales_prompt(text, products), SALES_RESPONSE_SCHEMA, "sales", SalesExtraction)
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

    data_url = f"data:{image_mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    content = [
        {"type": "image_url", "image_url": {"url": data_url}},
        {"type": "text", "text": stock_prompt(products, note=note)},
    ]
    payload = await _extract_with_retry(content, STOCK_RESPONSE_SCHEMA, "stock", StockExtraction)
    return _normalize_stock_lines(payload.lines)


async def extract_order_changes(products: list[dict], message: str, current_order: str) -> list[OrderChange]:
    """Cambios que el tendero pide al pedido sugerido ("quita el arroz", "ponle 2 cajas más de coca")."""
    text = (message or "").strip()
    if not text:
        raise ValueError("message is required")
    payload = await _extract_with_retry(
        order_changes_prompt(text, products, current_order),
        ORDER_CHANGES_RESPONSE_SCHEMA,
        "order_changes",
        OrderChangesExtraction,
    )
    return [
        change.model_copy(update={"sku": _blank_to_none(change.sku), "product_text": change.product_text.strip()})
        for change in payload.changes
    ]
