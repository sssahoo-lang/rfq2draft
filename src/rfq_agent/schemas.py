"""Pydantic data contracts passed between RFQ pipeline stages."""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    BeforeValidator,
    field_validator,
)


def _parse_decimal(value: Any) -> Decimal | None:
    """Coerce str/int/Decimal into Decimal; reject float to protect money precision."""
    if value is None:
        return None
    if isinstance(value, float):
        raise TypeError(
            "float is not allowed for Decimal fields; use str or Decimal"
        )
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (str, int)):
        return Decimal(str(value))
    raise TypeError(f"cannot coerce {type(value)!r} to Decimal")


DecimalStr = Annotated[
    Decimal,
    BeforeValidator(_parse_decimal),
    PlainSerializer(lambda v: format(v, "f"), return_type=str, when_used="json"),
]


class StrictModel(BaseModel):
    """Shared base: forbid unknown fields so hand-edited JSON typos fail loudly."""

    model_config = ConfigDict(extra="forbid")


class SourceType(str, Enum):
    """Inbound RFQ file format."""

    pdf = "pdf"
    eml = "eml"


class MatchStatus(str, Enum):
    """Catalog match band; unknown_sku and no_match are distinct for reviewers."""

    exact_sku = "exact_sku"
    attribute_match = "attribute_match"
    low_confidence = "low_confidence"
    unknown_sku = "unknown_sku"
    no_match = "no_match"


class PackageStatus(str, Enum):
    """Lifecycle of the reviewable quote package."""

    pending_review = "pending_review"
    approved = "approved"
    rejected = "rejected"


class UOM(str, Enum):
    """Unit of measure for catalog and quote lines."""

    ft = "ft"
    ea = "ea"


class Product(StrictModel):
    """Produced by catalog loader; consumed by match and enrich nodes."""

    sku: str
    description: str
    category: str
    hose_id_in: str | None = None
    construction: str | None = None
    working_pressure_psi: int | None = None
    material: str | None = None
    length_ft: DecimalStr | None = None
    end_a: str | None = None
    end_b: str | None = None
    unit_price_usd: DecimalStr
    uom: UOM
    lead_time_days: int
    stock_qty: int


class RFQDocument(StrictModel):
    """Produced by ingest; consumed by extract."""

    run_id: str
    source_path: str
    source_type: SourceType
    sender: str | None = None
    subject: str | None = None
    sent_date: str | None = None
    raw_text: str


class LineAttributes(StrictModel):
    """Produced by extract (per line); consumed by the attribute match rung."""

    hose_id_in: str | None = None
    construction: str | None = None
    working_pressure_psi: int | None = None
    material: str | None = None
    length_ft: DecimalStr | None = None
    end_a: str | None = None
    end_b: str | None = None
    category_hint: str | None = None


class ExtractedLine(StrictModel):
    """Produced by extract; consumed by match and carried into QuoteLine."""

    line_no: int
    source_text: str
    sku: str | None = None
    description: str | None = None
    quantity: DecimalStr
    uom: UOM | None = None
    attributes: LineAttributes
    requested_delivery: str | None = None
    notes: str | None = None
    extraction_confidence: float

    @field_validator("quantity")
    @classmethod
    def quantity_must_be_positive(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("quantity must be > 0")
        return value

    @field_validator("extraction_confidence")
    @classmethod
    def confidence_in_unit_interval(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("extraction_confidence must be between 0.0 and 1.0")
        return value


class ExtractedRFQ(StrictModel):
    """Produced by extract; consumed by match / assemble for header fields."""

    customer_name: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    rfq_date: str | None = None
    needed_by: str | None = None
    ship_to: str | None = None
    terms: str | None = None
    document_notes: str | None = None
    lines: list[ExtractedLine]

    @field_validator("lines")
    @classmethod
    def at_least_one_line(cls, value: list[ExtractedLine]) -> list[ExtractedLine]:
        if len(value) < 1:
            raise ValueError("lines must contain at least 1 ExtractedLine")
        return value


class MatchCandidate(StrictModel):
    """Produced by match (top-3 / nearest-SKU hints); consumed by review JSON."""

    sku: str
    score: float
    breakdown: dict[str, str]
    note: str | None = None


class MatchResult(StrictModel):
    """Produced by match; consumed by enrich and the human review package."""

    status: MatchStatus
    matched_sku: str | None = None
    score: float | None = None
    rationale: str
    candidates: list[MatchCandidate] = Field(default_factory=list)
    needs_human_review: bool


class QuoteLine(StrictModel):
    """Produced by enrich/assemble; consumed by human review and finalize."""

    line_no: int
    source_text: str
    extracted: ExtractedLine
    match: MatchResult
    catalog_description: str | None = None
    unit_price: DecimalStr | None = None
    uom: UOM | None = None
    lead_time_days: int | None = None
    stock_qty: int | None = None
    extended_price: DecimalStr | None = None
    flags: list[str] = Field(default_factory=list)


class LineOverride(StrictModel):
    """Produced by human reviewer edits; consumed by validate_edits / finalize."""

    line_no: int
    action: str
    replacement_sku: str | None = None
    note: str | None = None


class QuotePackage(StrictModel):
    """Produced by assemble; edited by human; consumed by finalize and mocks."""

    run_id: str
    quote_id: str
    status: PackageStatus = PackageStatus.pending_review
    customer_name: str | None = None
    contact_email: str | None = None
    rfq_date: str | None = None
    needed_by: str | None = None
    ship_to: str | None = None
    terms: str | None = None
    lines: list[QuoteLine]
    subtotal: DecimalStr
    flag_summary: list[str] = Field(default_factory=list)
    email_to: str | None = None
    email_subject: str | None = None
    email_body: str | None = None
    approved: bool = False
    reviewer_notes: str | None = None
    overrides: list[LineOverride] = Field(default_factory=list)
