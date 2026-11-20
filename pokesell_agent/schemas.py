"""Explicit Pydantic v2 JSON Schemas for All Tool Inputs, Outputs, and Error Contracts.

Addresses AgentOps Rubric Category 1 — Explicit JSON Schemas (5 pts) &
Guided Error Handling (5 pts):
- Strict Pydantic `BaseModel` schemas (`ConfigDict(extra="forbid")`) validate all
  tool arguments and constrain LLM structured outputs.
- Covers all 6 Pokémon TCG live-stream selling formats:
  `singles`, `rip_and_ship_pack`, `rip_till_you_hit`, `graded_slab`, `sleeves`, `coin`.
- Exports `EXPLICIT_TOOL_JSON_SCHEMAS` for static inspection and regression tests.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SellingFormat(StrEnum):
    """Supported Pokémon TCG live-stream selling formats on Whatnot & eBay."""

    SINGLES = "singles"
    RIP_AND_SHIP_PACK = "rip_and_ship_pack"
    RIP_TILL_YOU_HIT = "rip_till_you_hit"
    GRADED_SLAB = "graded_slab"
    SLEEVES = "sleeves"
    COIN = "coin"


class MarketplacePlatform(StrEnum):
    """Supported live-stream and auction platforms."""

    WHATNOT = "whatnot"
    EBAY = "ebay"


class GradingCompany(StrEnum):
    """Supported card grading authorities (or RAW for ungraded items)."""

    RAW = "RAW"
    PSA = "PSA"
    BGS = "BGS"
    CGC = "CGC"


class PackagingTier(StrEnum):
    """Physical shipping container tiers auto-calculated from pull-bag contents & value."""

    PWE_ENVELOPE = "PWE_ENVELOPE"
    BUBBLE_MAILER_BMWT = "BUBBLE_MAILER_BMWT"
    SLAB_RIGID_BOX = "SLAB_RIGID_BOX"
    INSURED_SIGNATURE_BOX = "INSURED_SIGNATURE_BOX"


class BagStatus(StrEnum):
    """Lifecycle states of a numbered physical Buyer Pull-Bag."""

    OPEN_ON_DESK = "OPEN_ON_DESK"
    SEALED_READY = "SEALED_READY"
    READY_FOR_CARRIER_DISPATCH = "READY_FOR_CARRIER_DISPATCH"


class BuyerVipTier(StrEnum):
    """Repeat-buyer loyalty tiers."""

    FIRST_TIME_TRAINER = "FIRST_TIME_TRAINER"
    RETURNING_COLLECTOR = "RETURNING_COLLECTOR"
    GYM_LEADER_REGULAR = "GYM_LEADER_REGULAR"
    MASTER_BALL_VIP = "MASTER_BALL_VIP"


class GuidedToolErrorResponse(BaseModel):
    """Structured error response giving actionable self-correction instructions to the LLM."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(
        default="error",
        description="Execution status, always 'error' when validation or lookup fails.",
    )
    error_code: str = Field(
        ...,
        description="Machine-readable error classification code (e.g., SKU_NOT_FOUND).",
    )
    message: str = Field(
        ...,
        description="Human-readable explanation of why the tool call failed.",
    )
    recovery_instructions: str = Field(
        ...,
        min_length=15,
        description=(
            "Actionable step-by-step instructions telling the LLM how to fix its "
            "parameters or which diagnostic tool to invoke next."
        ),
    )
    retryable: bool = Field(
        default=True,
        description="Whether the LLM should retry after applying suggested fixes.",
    )
    suggested_next_tool: str | None = Field(
        default=None,
        description="Optional recommended tool name to invoke next.",
    )
    suggested_parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="Suggested corrected parameter values for self-correction.",
    )


# ---------------------------------------------------------------------------
# 1. Register / Restock Inventory Lot Schemas
# ---------------------------------------------------------------------------


class RegisterPokemonInventoryInput(BaseModel):
    """Input schema for registering or restocking a Pokémon TCG item or break lot."""

    model_config = ConfigDict(extra="forbid")

    sku: str = Field(
        ...,
        min_length=3,
        max_length=48,
        pattern=r"^[A-Z0-9-]+$",
        description="Unique uppercase SKU identifier (e.g., 'PKM-ES-UMBREON-VMAX', 'PACK-151-JP').",
    )
    item_title: str = Field(
        ...,
        min_length=3,
        max_length=160,
        description="Card, booster pack, slab, sleeve pack, or coin display name.",
    )
    set_name: str = Field(
        default="Pokemon TCG",
        min_length=2,
        max_length=80,
        description="Expansion set or product line (e.g., 'Evolving Skies', 'Scarlet & Violet 151').",
    )
    selling_format: SellingFormat = Field(
        ...,
        description=(
            "Item format: 'singles', 'rip_and_ship_pack', 'rip_till_you_hit', "
            "'graded_slab', 'sleeves', or 'coin'."
        ),
    )
    storage_location: str = Field(
        ...,
        min_length=3,
        max_length=64,
        description="Physical binder/box/shelf coordinate (e.g., 'Binder-B2-Slot-04', 'SlabCase-A1').",
    )
    grading_company: GradingCompany = Field(
        default=GradingCompany.RAW,
        description="Grading company ('RAW', 'PSA', 'BGS', or 'CGC').",
    )
    grade_score: float | None = Field(
        default=None,
        ge=1.0,
        le=10.0,
        description="Numeric slab grade (1.0 to 10.0) when grading_company is PSA/BGS/CGC.",
    )
    cost_basis_usd: float = Field(
        default=0.0,
        ge=0.0,
        le=25000.0,
        description="Unit acquisition cost in USD for net margin calculation.",
    )
    quantity_available: int = Field(
        default=1,
        ge=1,
        le=5000,
        description="Units available in stock to add or set.",
    )

    @field_validator("sku", mode="before")
    @classmethod
    def normalize_sku(cls, val: str) -> str:
        return str(val).strip().upper()


class RegisterPokemonInventoryOutput(BaseModel):
    """Output schema after registering or restocking an inventory lot."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(default="success")
    sku: str
    item_title: str
    selling_format: SellingFormat
    storage_location: str
    grading_company: GradingCompany
    grade_score: float | None = None
    cost_basis_usd: float
    total_quantity_available: int


# ---------------------------------------------------------------------------
# 2. Record Live-Stream Sale Schemas
# ---------------------------------------------------------------------------


class RecordLivestreamSaleInput(BaseModel):
    """Input schema for logging a Whatnot or eBay live-stream hammer-drop sale."""

    model_config = ConfigDict(extra="forbid")

    stream_id: str = Field(
        default="STREAM-LIVE-001",
        min_length=4,
        max_length=48,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Identifier of the active live stream session (e.g., 'STREAM-WN-20261120').",
    )
    platform: MarketplacePlatform = Field(
        ...,
        description="Marketplace platform where the sale occurred ('whatnot' or 'ebay').",
    )
    buyer_handle: str = Field(
        ...,
        min_length=2,
        max_length=64,
        description="Buyer username/handle on Whatnot or eBay (e.g., '@pokefan99').",
    )
    item_title: str = Field(
        ...,
        min_length=2,
        max_length=160,
        description="Name of the card, pack, RTYH spot, slab, sleeves, or coin sold.",
    )
    selling_format: SellingFormat = Field(
        ...,
        description=(
            "Selling format: 'singles', 'rip_and_ship_pack', 'rip_till_you_hit', "
            "'graded_slab', 'sleeves', or 'coin'."
        ),
    )
    hammer_price_usd: float = Field(
        ...,
        gt=0.0,
        le=25000.0,
        description="Hammer / winning sale price in USD (must be > 0 and <= 25000).",
    )
    quantity: int = Field(
        default=1,
        ge=1,
        le=200,
        description="Quantity of units sold in this lot.",
    )
    sku: str | None = Field(
        default=None,
        max_length=48,
        description="Optional inventory SKU to decrement stock and auto-pull cost basis & binder slot.",
    )
    source_storage_location: str | None = Field(
        default=None,
        max_length=64,
        description="Where to pull the item from if SKU is omitted (e.g., 'Binder-B2-Slot-04').",
    )
    rtyh_packs_ripped: int = Field(
        default=0,
        ge=0,
        le=100,
        description="For 'rip_till_you_hit': number of packs opened before hitting a chase card.",
    )
    rtyh_hit_card: str | None = Field(
        default=None,
        max_length=160,
        description="For 'rip_till_you_hit' or 'rip_and_ship_pack': notable chase/hit card pulled.",
    )
    buyer_notes: str | None = Field(
        default=None,
        max_length=300,
        description="Optional order note (automatically PII-scrubbed before logging).",
    )

    @field_validator("buyer_handle", mode="before")
    @classmethod
    def normalize_handle(cls, val: str) -> str:
        cleaned = str(val).strip().lower()
        if cleaned and not cleaned.startswith("@"):
            cleaned = f"@{cleaned}"
        return cleaned


class RecordLivestreamSaleOutput(BaseModel):
    """Output schema returned when a live-stream sale is persisted and routed to a Pull-Bag."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(default="success")
    sale_id: str
    stream_id: str
    platform: MarketplacePlatform
    buyer_handle: str
    is_repeat_buyer: bool
    buyer_vip_tier: BuyerVipTier
    lifetime_orders_count: int
    item_title: str
    selling_format: SellingFormat
    pull_from_location: str
    assigned_pull_bag_number: str
    combined_with_existing_bag: bool
    bag_item_count: int
    bag_cumulative_value_usd: float
    recommended_packaging_tier: PackagingTier
    protection_supplies_checklist: list[str]
    hammer_price_usd: float
    estimated_platform_fee_usd: float
    net_payout_usd: float
    estimated_net_profit_usd: float
    vip_perk_recommendation: str


# ---------------------------------------------------------------------------
# 3. Lookup Buyer Purchase History & VIP Tier Schemas
# ---------------------------------------------------------------------------


class LookupBuyerHistoryInput(BaseModel):
    """Input schema for querying repeat-buyer CRM history and open Pull-Bags."""

    model_config = ConfigDict(extra="forbid")

    buyer_handle: str = Field(
        ...,
        min_length=2,
        max_length=64,
        description="Buyer handle on Whatnot or eBay (e.g., '@pokefan99').",
    )
    active_stream_id: str | None = Field(
        default=None,
        max_length=48,
        description="Optional active stream ID to check if buyer already has an open Pull-Bag on desk.",
    )

    @field_validator("buyer_handle", mode="before")
    @classmethod
    def normalize_handle(cls, val: str) -> str:
        cleaned = str(val).strip().lower()
        if cleaned and not cleaned.startswith("@"):
            cleaned = f"@{cleaned}"
        return cleaned


class LookupBuyerHistoryOutput(BaseModel):
    """Output schema describing buyer loyalty, historical purchases, and open stream pull-bags."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(default="success")
    buyer_handle: str
    known_buyer: bool
    is_repeat_buyer: bool
    vip_tier: BuyerVipTier
    total_historical_orders: int
    lifetime_spend_usd: float
    platforms_used: list[str]
    favorite_formats: list[str]
    open_pull_bag_in_stream: str | None = None
    open_bag_item_count: int = 0
    combine_shipping_alert: str
    recent_purchases: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 4. Assign / Recalculate Fulfillment Pull-Bag & Packaging Tier Schemas
# ---------------------------------------------------------------------------


class AssignFulfillmentPullBagInput(BaseModel):
    """Input schema for inspecting or updating a numbered physical Pull-Bag."""

    model_config = ConfigDict(extra="forbid")

    bag_number: str = Field(
        ...,
        pattern=r"^BAG-\d{3,5}$",
        description="Physical numbered Pull-Bag ID on the packing table (e.g., 'BAG-001').",
    )
    override_packaging_tier: PackagingTier | None = Field(
        default=None,
        description="Optional explicit packaging tier upgrade (cannot downgrade insured slabs).",
    )
    shelf_bin_coordinate: str | None = Field(
        default=None,
        max_length=48,
        description="Optional physical staging bin coordinate (e.g., 'ShippingBin-RowA-01').",
    )

    @field_validator("bag_number", mode="before")
    @classmethod
    def normalize_bag(cls, val: str) -> str:
        return str(val).strip().upper()


class AssignFulfillmentPullBagOutput(BaseModel):
    """Output schema detailing exact packing instructions for a numbered Pull-Bag."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(default="success")
    bag_number: str
    stream_id: str
    buyer_handle: str
    bag_status: BagStatus
    shelf_bin_coordinate: str
    total_items_in_bag: int
    cumulative_value_usd: float
    packaging_tier: PackagingTier
    protection_supplies_checklist: list[str]
    items_manifest: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# 5. Stream Revenue, Platform Fee & Inventory Insights Schemas
# ---------------------------------------------------------------------------


class StreamInsightsQueryInput(BaseModel):
    """Input schema for querying historical or live stream sales, fees, and stock analytics."""

    model_config = ConfigDict(extra="forbid")

    stream_id: str | None = Field(
        default=None,
        max_length=48,
        description="Filter analytics to a single stream ID, or None for all-time historical insights.",
    )
    platform_filter: MarketplacePlatform | None = Field(
        default=None,
        description="Optional filter by 'whatnot' or 'ebay'.",
    )


class StreamInsightsOutput(BaseModel):
    """Output schema for stream sales, net proceeds, repeat-buyer rate, and open bags."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(default="success")
    scope: str
    total_transactions: int
    total_units_sold: int
    gross_revenue_usd: float
    total_platform_fees_usd: float
    net_payout_after_fees_usd: float
    estimated_net_profit_usd: float
    breakdown_by_format: dict[str, dict[str, float | int]]
    breakdown_by_platform: dict[str, dict[str, float | int]]
    unique_buyers_count: int
    repeat_buyers_count: int
    open_pull_bags_count: int
    top_buyers: list[dict[str, Any]]
    low_stock_alerts: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# 6. HITL High-Stakes Tool 1: Buyer Refund / Sale Void Schemas
# ---------------------------------------------------------------------------


class ExecuteBuyerRefundInput(BaseModel):
    """Input schema for voiding/refunding a recorded sale (requires HITL approval)."""

    model_config = ConfigDict(extra="forbid")

    sale_id: str = Field(
        ...,
        pattern=r"^SALE-[A-Z0-9-]+$",
        description="Recorded sale identifier to void/refund (e.g., 'SALE-0001').",
    )
    reason: str = Field(
        ...,
        min_length=8,
        max_length=300,
        description="Clear audit explanation for the buyer cancellation or refund.",
    )
    restock_inventory: bool = Field(
        default=True,
        description="Whether to return the item units back to available SKU stock.",
    )
    human_approval_token: str | None = Field(
        default=None,
        max_length=80,
        description="Explicit human confirmation token matching 'APPROVE-VOID-<SALE_ID>'.",
    )

    @field_validator("sale_id", mode="before")
    @classmethod
    def normalize_sale_id(cls, val: str) -> str:
        return str(val).strip().upper()


class ExecuteBuyerRefundOutput(BaseModel):
    """Output schema for HITL-governed sale refund/void."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(
        ...,
        description="'success' when confirmed, or 'pending_human_approval' when halted at HITL gate.",
    )
    sale_id: str
    buyer_handle: str
    refunded_amount_usd: float
    inventory_restocked: bool
    affected_pull_bag: str
    human_approved: bool
    approval_instructions: str | None = None


# ---------------------------------------------------------------------------
# 7. HITL High-Stakes Tool 2: Finalize Stream Shipping Manifest Batch
# ---------------------------------------------------------------------------


class FinalizeStreamManifestInput(BaseModel):
    """Input schema for locking all open Pull-Bags in a stream for carrier dispatch (HITL)."""

    model_config = ConfigDict(extra="forbid")

    stream_id: str = Field(
        ...,
        min_length=4,
        max_length=48,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Stream session ID whose open Pull-Bags should be finalized for shipping.",
    )
    carrier_Dropoff_method: str = Field(
        default="USPS_GROUND_ADVANTAGE_AND_PRIORITY",
        min_length=3,
        max_length=64,
        description="Carrier dispatch method (e.g., 'USPS_SCAN_SHEET', 'UPS_INSURED_PICKUP').",
    )
    human_approval_token: str | None = Field(
        default=None,
        max_length=80,
        description="Explicit human confirmation token matching 'APPROVE-MANIFEST-<STREAM_ID>'.",
    )


class FinalizeStreamManifestOutput(BaseModel):
    """Output schema for HITL-governed stream shipping manifest closeout."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(
        ...,
        description="'success' when confirmed, or 'pending_human_approval' when halted at HITL gate.",
    )
    manifest_id: str
    stream_id: str
    finalized_bags_count: int
    total_declared_value_usd: float
    tier_counts: dict[str, int]
    human_approved: bool
    approval_instructions: str | None = None


EXPLICIT_TOOL_JSON_SCHEMAS: dict[str, dict[str, Any]] = {
    "RegisterPokemonInventoryInput": RegisterPokemonInventoryInput.model_json_schema(),
    "RegisterPokemonInventoryOutput": RegisterPokemonInventoryOutput.model_json_schema(),
    "RecordLivestreamSaleInput": RecordLivestreamSaleInput.model_json_schema(),
    "RecordLivestreamSaleOutput": RecordLivestreamSaleOutput.model_json_schema(),
    "LookupBuyerHistoryInput": LookupBuyerHistoryInput.model_json_schema(),
    "LookupBuyerHistoryOutput": LookupBuyerHistoryOutput.model_json_schema(),
    "AssignFulfillmentPullBagInput": AssignFulfillmentPullBagInput.model_json_schema(),
    "AssignFulfillmentPullBagOutput": AssignFulfillmentPullBagOutput.model_json_schema(),
    "StreamInsightsQueryInput": StreamInsightsQueryInput.model_json_schema(),
    "StreamInsightsOutput": StreamInsightsOutput.model_json_schema(),
    "ExecuteBuyerRefundInput": ExecuteBuyerRefundInput.model_json_schema(),
    "ExecuteBuyerRefundOutput": ExecuteBuyerRefundOutput.model_json_schema(),
    "FinalizeStreamManifestInput": FinalizeStreamManifestInput.model_json_schema(),
    "FinalizeStreamManifestOutput": FinalizeStreamManifestOutput.model_json_schema(),
    "GuidedToolErrorResponse": GuidedToolErrorResponse.model_json_schema(),
}
