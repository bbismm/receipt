"""Adapter contract — every venue adapter implements this protocol (SPEC §6)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class MatchResult:
    """SPEC §7 match object. id_match priority over price_match."""
    symbol: str = ""
    side: bool = False
    size: bool = False
    price_match: bool = False
    time_match: bool = False
    id_match: bool = False
    tolerance_used: dict = field(default_factory=lambda: {"price": 0.002, "time_ms": 5000})
    notes: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """Match-completeness rule (§7): id_match required, all others true."""
        return (
            self.id_match
            and bool(self.symbol)
            and self.side
            and self.size
            and self.price_match
        )

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "size": self.size,
            "price_match": self.price_match,
            "time_match": self.time_match,
            "id_match": self.id_match,
            "tolerance_used": self.tolerance_used,
            **({"notes": self.notes} if self.notes else {}),
        }


class ReconciliationAdapter(Protocol):
    """Protocol for venue adapters."""

    venue: str

    def fetch_order(self, order_id: str) -> dict: ...
    def fetch_position(self, symbol: str) -> dict: ...
    def fetch_balance(self) -> dict: ...

    def reconcile_claim(
        self,
        claim_data: dict,
        external_action_data: dict,
        *,
        price_tolerance: float = 0.002,
        time_tolerance_ms: int = 5000,
    ) -> MatchResult: ...
