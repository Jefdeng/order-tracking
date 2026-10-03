"""Pydantic models. ExtractionResult is the schema the LLM must return."""
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

EventType = Literal["order_confirmed", "in_production", "shipped", "delivered",
                    "delayed", "backordered", "cancelled", "other"]


class LineItem(BaseModel):
    sku: Optional[str] = Field(default=None, description="SKU / item number exactly as written in the email.")
    name: Optional[str] = Field(default=None, description="Item name as written in the email.")
    qty: Optional[int] = Field(default=None, description="Quantity, only if stated.")


class OrderUpdate(BaseModel):
    order_number: Optional[str] = Field(
        default=None, description="The vendor's order / confirmation number exactly as written, without a label.")
    vendor: Optional[str] = Field(default=None, description="Vendor or store name.")
    event: EventType = Field(description="The single best description of what this email reports.")
    event_date: Optional[str] = Field(
        default=None, description="Date the event happened if the email states it, YYYY-MM-DD. Else null.")
    est_arrival: Optional[str] = Field(
        default=None, description="Specific expected delivery date, YYYY-MM-DD. Ranges/durations go in lead_time.")
    lead_time: Optional[str] = Field(default=None, description="Lead time or delivery window as written.")
    tracking_number: Optional[str] = Field(default=None, description="Carrier tracking number.")
    carrier: Optional[str] = Field(default=None, description="Carrier name, e.g. UPS, FedEx.")
    lines: List[LineItem] = Field(default_factory=list, description="Items the email specifically mentions.")
    matched_rows: List[int] = Field(
        default_factory=list,
        description="Row numbers from TRACKED ROWS this update refers to; only if you can tell, else empty.")
    notes: Optional[str] = Field(
        default=None, description="One concise sentence of useful detail not captured by other fields.")


class ExtractionResult(BaseModel):
    is_order_related: bool
    updates: List[OrderUpdate] = Field(default_factory=list)
