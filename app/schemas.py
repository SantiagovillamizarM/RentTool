from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    distributor_id: int
    sku: str
    name: str
    aliases: str
    presentation: str
    units_per_case: int
    unit_price: Decimal
    active: bool


class StoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    distributor_id: int
    name: str
    owner_name: str
    link_code: str
    telegram_chat_id: int | None
    visit_weekdays: str
    lead_time_days: int
    consent_at: datetime | None


class SaleLine(BaseModel):
    sku: str | None = None
    product_text: str = ""
    quantity: int = Field(gt=0)


class StockLine(BaseModel):
    sku: str | None = None
    quantity: int = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
