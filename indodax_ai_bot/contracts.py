"""Versioned, explicit contracts. No model output can authorize exchange execution."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


SCHEMA_VERSION = 1


class FinalDecision(str, Enum):
    PASS = "PASS"
    REJECT = "REJECT"


class RiskDecision(str, Enum):
    ALLOW = "ALLOW"
    NO_TRADE = "NO_TRADE"
    EXIT_REVIEW = "EXIT_REVIEW"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class SourceReceipt:
    pair: str
    closed_1m_epoch: int
    observed_epoch: int
    provenance: str
    verified: bool = False
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.pair.endswith("_idr") or self.closed_1m_epoch <= 0 or self.observed_epoch < self.closed_1m_epoch:
            raise ValueError("invalid market source receipt")


@dataclass(frozen=True)
class MarketSnapshot:
    source: SourceReceipt
    best_bid_idr: Decimal
    best_ask_idr: Decimal

    def __post_init__(self) -> None:
        if not (self.best_bid_idr.is_finite() and self.best_ask_idr.is_finite()):
            raise ValueError("nonfinite market price")
        if not (Decimal("0") < self.best_bid_idr <= self.best_ask_idr):
            raise ValueError("invalid bid/ask")


@dataclass(frozen=True)
class RiskVeto:
    decision: RiskDecision
    reason: str


@dataclass(frozen=True)
class TradeProposal:
    proposal_id: str
    source: SourceReceipt
    entry_idr: Decimal
    quantity: Decimal
    estimated_net_profit_idr: Decimal
    estimated_net_roi_percent: Decimal
    expires_epoch: int


@dataclass(frozen=True)
class OwnerDecision:
    proposal_id: str
    decision: FinalDecision
    decided_epoch: int
    owner_id: str


@dataclass(frozen=True)
class ExecutionIntent:
    intent_id: str
    proposal_id: str
    side: OrderSide
    quantity: Decimal


@dataclass(frozen=True)
class OrderState:
    intent_id: str
    exchange_order_id: str | None
    status: str
    filled_quantity: Decimal


@dataclass(frozen=True)
class Fill:
    exchange_order_id: str
    quantity: Decimal
    price_idr: Decimal
    fee_idr: Decimal


@dataclass(frozen=True)
class PositionLot:
    position_id: str
    asset: str
    bot_owned_quantity: Decimal
    cost_basis_idr: Decimal
