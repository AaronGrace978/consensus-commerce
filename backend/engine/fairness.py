"""Jain fairness index, surplus split, and veto rules for commerce deals.

Ported from the Skyline Consensus negotiation-core patterns (Jain index,
hard veto, auditable SHA-256 certificate) and re-skinned for price/terms.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def jain_index(utilities: list[float]) -> float:
    """Jain's fairness index in [0, 1].

    J(u) = (sum u_i)^2 / (n * sum u_i^2)

    Equal positive utilities yield 1.0. A 90/10 split is ~0.67.
    """
    values = [float(u) for u in utilities]
    if not values:
        return 0.0
    if any(v < 0 for v in values):
        raise ValueError("Jain index is undefined for negative utilities")
    total = sum(values)
    if total <= 0:
        return 0.0
    squares = sum(v * v for v in values)
    if squares <= 0:
        return 0.0
    return (total * total) / (len(values) * squares)


def surplus_split(price: float, seller_reservation: float, buyer_reservation: float) -> dict[str, float]:
    """Split the bargaining surplus at `price` between buyer and seller.

    Surplus = buyer_max - seller_min.
    Buyer share grows as price falls; seller share grows as price rises.
    """
    surplus = float(buyer_reservation) - float(seller_reservation)
    if surplus <= 0:
        return {"surplus": 0.0, "buyer": 0.5, "seller": 0.5}
    buyer_share = (float(buyer_reservation) - float(price)) / surplus
    seller_share = (float(price) - float(seller_reservation)) / surplus
    return {
        "surplus": round(surplus, 2),
        "buyer": round(buyer_share, 4),
        "seller": round(seller_share, 4),
    }


def nash_price(seller_reservation: float, buyer_reservation: float, buyer_weight: float, seller_weight: float) -> float:
    """Weighted Nash bargaining price inside the feasible surplus interval.

    Equal weights recover the midpoint. A higher seller weight pulls the
    clearing price up; a higher buyer weight pulls it down.
    """
    surplus = float(buyer_reservation) - float(seller_reservation)
    if surplus <= 0:
        return round(float(seller_reservation), 2)
    w_b = max(0.05, float(buyer_weight))
    w_s = max(0.05, float(seller_weight))
    seller_share = w_s / (w_b + w_s)
    return round(float(seller_reservation) + surplus * seller_share, 2)


def should_veto(
    *,
    agent_id: str,
    price: float,
    seller_reservation: float,
    buyer_reservation: float,
    jain: float,
    min_share: float = 0.18,
    min_jain: float = 0.82,
) -> tuple[bool, str]:
    """Hard veto rules — Skyline-style, applied to commerce.

    Buyer vetoes if the price exceeds reservation or their surplus share
    collapses. Seller vetoes if the price is below reservation. The
    fairness referee vetoes an inequitable Jain index.
    """
    split = surplus_split(price, seller_reservation, buyer_reservation)
    if agent_id == "BUYER":
        if price > buyer_reservation + 0.005:
            return True, "PRICE_ABOVE_RESERVATION"
        if split["buyer"] < min_share:
            return True, "SURPLUS_SHARE_TOO_SMALL"
        return False, ""
    if agent_id == "SELLER":
        if price < seller_reservation - 0.005:
            return True, "PRICE_BELOW_RESERVATION"
        if split["seller"] < min_share:
            return True, "SURPLUS_SHARE_TOO_SMALL"
        return False, ""
    if agent_id == "REFEREE":
        if jain < min_jain:
            return True, "JAIN_BELOW_THRESHOLD"
        if split["buyer"] < min_share or split["seller"] < min_share:
            return True, "UNBALANCED_SURPLUS"
        return False, ""
    return False, ""


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
