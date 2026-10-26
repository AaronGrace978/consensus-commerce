"""Buyer, Seller, and Fairness-referee agent profiles + utility scoring."""

from __future__ import annotations

from .fairness import jain_index, surplus_split

AGENT_PROFILES = {
    "BUYER": {
        "id": "BUYER",
        "name": "Buyer Procurement Agent",
        "shortName": "Buyer Agent",
        "role": "Price discipline, delivery speed, return protection",
        "organization": "Consensus Commerce — Demand Desk",
        "color": "#22d3ee",
        "badge": "PRICE + TERMS",
        "mandate": (
            "Hold the seller to the buyer's reservation, compress shipping "
            "and expand warranty/returns, and refuse any deal that starves "
            "the buyer of surplus."
        ),
        "disagreementPoint": 0.18,
    },
    "SELLER": {
        "id": "SELLER",
        "name": "Seller Revenue Agent",
        "shortName": "Seller Agent",
        "role": "Margin, fulfillment cost, liability control",
        "organization": "Consensus Commerce — Supply Desk",
        "color": "#f59e0b",
        "badge": "MARGIN + FULFILLMENT",
        "mandate": (
            "Defend the reservation floor, recover list-price margin, and "
            "keep warranty/return exposure commercially viable."
        ),
        "disagreementPoint": 0.18,
    },
    "REFEREE": {
        "id": "REFEREE",
        "name": "Fairness Referee Agent",
        "shortName": "Fairness Referee",
        "role": "Jain index, surplus split, veto of inequitable deals",
        "organization": "Consensus Commerce — Public Audit Bench",
        "color": "#10b981",
        "badge": "JAIN + VETO",
        "mandate": (
            "Veto any proposal whose Jain fairness index or surplus split "
            "fails the published threshold, then mint a SHA-256 proof "
            "once consensus is individually rational for both desks."
        ),
        "disagreementPoint": 0.72,
    },
}


def score_deal(
    *,
    price: float,
    seller_reservation: float,
    buyer_reservation: float,
    shipping_days: int,
    warranty_months: int,
    return_days: int,
    shipping_buyer: int,
    shipping_seller: int,
    warranty_buyer: int,
    warranty_seller: int,
    return_buyer: int,
    return_seller: int,
    weights: dict[str, float] | None = None,
) -> dict:
    """Score a proposed deal for all three agents (0–100 utilities)."""
    split = surplus_split(price, seller_reservation, buyer_reservation)
    surplus = split["surplus"]
    buyer_share = split["buyer"]
    seller_share = split["seller"]

    # Terms scored as closeness to each desk's aspiration (0–1).
    ship_span = max(1, shipping_seller - shipping_buyer)
    buyer_ship = max(0.0, min(1.0, (shipping_seller - shipping_days) / ship_span))
    seller_ship = 1.0 - buyer_ship

    war_span = max(1, warranty_buyer - warranty_seller)
    buyer_war = max(0.0, min(1.0, (warranty_months - warranty_seller) / war_span))
    seller_war = 1.0 - buyer_war

    ret_span = max(1, return_buyer - return_seller)
    buyer_ret = max(0.0, min(1.0, (return_days - return_seller) / ret_span))
    seller_ret = 1.0 - buyer_ret

    terms_buyer = 0.45 * buyer_ship + 0.30 * buyer_war + 0.25 * buyer_ret
    terms_seller = 0.45 * seller_ship + 0.30 * seller_war + 0.25 * seller_ret

    buyer_u = 100.0 * (0.72 * max(0.0, min(1.0, buyer_share)) + 0.28 * terms_buyer)
    seller_u = 100.0 * (0.72 * max(0.0, min(1.0, seller_share)) + 0.28 * terms_seller)

    jain = jain_index([max(0.0, buyer_share), max(0.0, seller_share)])
    # Referee utility is the fairness itself, penalized if either side is crushed.
    balance_penalty = 0.0
    if buyer_share < 0.22 or seller_share < 0.22:
        balance_penalty = 18.0
    referee_u = max(8.0, min(99.0, 100.0 * jain - balance_penalty))

    buyer_u = round(max(8.0, min(98.0, buyer_u)), 1)
    seller_u = round(max(8.0, min(98.0, seller_u)), 1)
    referee_u = round(referee_u, 1)

    w = weights or {"buyer": 0.34, "seller": 0.34, "referee": 0.32}
    w_sum = max(1e-6, w.get("buyer", 0.34) + w.get("seller", 0.34) + w.get("referee", 0.32))
    w_b = w.get("buyer", 0.34) / w_sum
    w_s = w.get("seller", 0.34) / w_sum
    w_r = w.get("referee", 0.32) / w_sum

    weighted = w_b * buyer_u + w_s * seller_u + w_r * referee_u
    spread = max(buyer_u, seller_u, referee_u) - min(buyer_u, seller_u, referee_u)
    consensus = round(max(10.0, min(98.0, weighted - 0.18 * spread)), 1)

    nash = (max(1.0, buyer_u - 18) ** w_b) * (max(1.0, seller_u - 18) ** w_s) * (max(1.0, referee_u - 72) ** w_r)

    return {
        "buyerUtility": buyer_u,
        "sellerUtility": seller_u,
        "refereeUtility": referee_u,
        "consensusScore": consensus,
        "nashBargainingIndex": round(nash, 2),
        "jainFairnessIndex": round(jain, 4),
        "surplus": surplus,
        "surplusSplit": {"buyer": round(buyer_share, 4), "seller": round(seller_share, 4)},
        "weights": {
            "buyer": round(w_b, 2),
            "seller": round(w_s, 2),
            "referee": round(w_r, 2),
        },
    }
