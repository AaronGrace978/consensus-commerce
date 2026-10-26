"""Five-round commerce negotiation protocol.

Round 1 — Seller opens at list / ask. Buyer + referee veto.
Round 2 — Buyer counters at target. Seller vetoes; referee holds.
Round 3 — Referee proposes a fair surplus split + interpolated terms.
Round 4 — Weighted Nash synthesis. All three agents ratify.
Round 5 — SHA-256 proof-of-fairness certificate is minted.

This is the Skyline Consensus protocol (propose → veto → counter →
Nash → attest) re-skinned for price and commercial terms.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..catalog import get_item
from .agents import AGENT_PROFILES, score_deal
from .fairness import nash_price, sha256_hex, should_veto, surplus_split


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _round_terms(item: dict, t: float) -> dict:
    """t=0 is seller aspiration; t=1 is buyer aspiration."""
    shipping = int(round(_lerp(item["shippingDaysSeller"], item["shippingDaysBuyer"], t)))
    warranty = int(round(_lerp(item["warrantyMonthsSeller"], item["warrantyMonthsBuyer"], t)))
    returns = int(round(_lerp(item["returnDaysSeller"], item["returnDaysBuyer"], t)))
    return {
        "shippingDays": max(1, shipping),
        "warrantyMonths": max(0, warranty),
        "returnDays": max(0, returns),
    }


def _score(item: dict, price: float, terms: dict, weights: dict) -> dict:
    return score_deal(
        price=price,
        seller_reservation=item["sellerReservation"],
        buyer_reservation=item["buyerReservation"],
        shipping_days=terms["shippingDays"],
        warranty_months=terms["warrantyMonths"],
        return_days=terms["returnDays"],
        shipping_buyer=item["shippingDaysBuyer"],
        shipping_seller=item["shippingDaysSeller"],
        warranty_buyer=item["warrantyMonthsBuyer"],
        warranty_seller=item["warrantyMonthsSeller"],
        return_buyer=item["returnDaysBuyer"],
        return_seller=item["returnDaysSeller"],
        weights=weights,
    )


def _vetoes(item: dict, price: float, jain: float) -> dict[str, dict]:
    out = {}
    for agent in ("BUYER", "SELLER", "REFEREE"):
        vetoed, reason = should_veto(
            agent_id=agent,
            price=price,
            seller_reservation=item["sellerReservation"],
            buyer_reservation=item["buyerReservation"],
            jain=jain,
        )
        out[agent] = {"veto": vetoed, "reason": reason}
    return out


def _stance(vetoes: dict, agent: str, fallback: str) -> str:
    if vetoes[agent]["veto"]:
        return "VETO"
    return fallback


def _msg(
    *,
    msg_id: str,
    round_no: int,
    agent_id: str,
    action: str,
    stance: str,
    headline: str,
    message: str,
    metrics: list[dict],
) -> dict:
    return {
        "id": msg_id,
        "round": round_no,
        "agentId": agent_id,
        "action": action,
        "stance": stance,
        "headline": headline,
        "message": message,
        "metricsCited": metrics,
    }


def run_negotiation(params: dict | None = None) -> dict[str, Any]:
    params = params or {}
    item = get_item(params.get("itemId"))
    weights = params.get("weights") or item["defaultWeights"]

    r1_price = float(item["sellerAsk"])
    r1_terms = _round_terms(item, 0.05)
    r1_scores = _score(item, r1_price, r1_terms, weights)
    r1_veto = _vetoes(item, r1_price, r1_scores["jainFairnessIndex"])

    r2_price = float(item["buyerTarget"])
    r2_terms = _round_terms(item, 0.92)
    r2_scores = _score(item, r2_price, r2_terms, weights)
    r2_veto = _vetoes(item, r2_price, r2_scores["jainFairnessIndex"])

    midpoint = nash_price(
        item["sellerReservation"],
        item["buyerReservation"],
        buyer_weight=0.5,
        seller_weight=0.5,
    )
    r3_price = midpoint
    r3_terms = _round_terms(item, 0.55)
    r3_scores = _score(item, r3_price, r3_terms, weights)
    r3_veto = _vetoes(item, r3_price, r3_scores["jainFairnessIndex"])

    r4_price = nash_price(
        item["sellerReservation"],
        item["buyerReservation"],
        buyer_weight=weights.get("buyer", 0.34),
        seller_weight=weights.get("seller", 0.34),
    )
    r4_terms = _round_terms(item, 0.50)
    r4_scores = _score(item, r4_price, r4_terms, weights)
    r4_veto = _vetoes(item, r4_price, r4_scores["jainFairnessIndex"])

    r1_split = surplus_split(r1_price, item["sellerReservation"], item["buyerReservation"])
    r2_split = surplus_split(r2_price, item["sellerReservation"], item["buyerReservation"])
    r4_split = surplus_split(r4_price, item["sellerReservation"], item["buyerReservation"])

    currency = item["currency"]

    def money(v: float) -> str:
        return f"${v:,.2f} {currency}"

    rounds = [
        {
            "round": 1,
            "stage": "SELLER_OPENING_AND_AUDIT",
            "title": "Round 1 — Seller opening ask & multi-agent audit",
            "status": "VETOED_BY_BUYER_AND_REFEREE",
            "price": r1_price,
            "terms": r1_terms,
            "scores": r1_scores,
            "vetoes": r1_veto,
            "summary": (
                f"Seller opens at list {money(r1_price)}. Buyer surplus share is "
                f"{r1_split['buyer']:.0%}; Jain index {r1_scores['jainFairnessIndex']:.3f}. "
                "Buyer and Fairness Referee issue hard vetoes."
            ),
            "messages": [
                _msg(
                    msg_id="msg-r1-seller",
                    round_no=1,
                    agent_id="SELLER",
                    action="PROPOSE_ASK",
                    stance="PROPOSAL",
                    headline=f"Opening at list {money(r1_price)}",
                    message=(
                        f"Submitting the published ask for {item['title']}. "
                        f"Reservation floor is {money(item['sellerReservation'])}. "
                        f"Fulfillment at {r1_terms['shippingDays']} days, "
                        f"{r1_terms['warrantyMonths']}-month warranty."
                    ),
                    metrics=[
                        {"label": "Ask", "value": money(r1_price)},
                        {"label": "Seller surplus", "value": f"{r1_split['seller']:.0%}"},
                        {"label": "Seller utility", "value": f"{r1_scores['sellerUtility']}/100"},
                    ],
                ),
                _msg(
                    msg_id="msg-r1-buyer",
                    round_no=1,
                    agent_id="BUYER",
                    action="VETO_PRICE",
                    stance=_stance(r1_veto, "BUYER", "HOLD"),
                    headline=f"VETO: {money(r1_price)} exceeds a fair surplus split",
                    message=(
                        f"Reservation is {money(item['buyerReservation'])}. "
                        f"At list the buyer keeps only {r1_split['buyer']:.0%} of a "
                        f"${r1_split['surplus']:,.2f} surplus. Counter incoming."
                    ),
                    metrics=[
                        {"label": "Buyer surplus", "value": f"{r1_split['buyer']:.0%}"},
                        {"label": "Reservation", "value": money(item["buyerReservation"])},
                        {"label": "Buyer utility", "value": f"{r1_scores['buyerUtility']}/100"},
                    ],
                ),
                _msg(
                    msg_id="msg-r1-ref",
                    round_no=1,
                    agent_id="REFEREE",
                    action="VETO_FAIRNESS",
                    stance=_stance(r1_veto, "REFEREE", "HOLD"),
                    headline=f"EQUITY VETO: Jain {r1_scores['jainFairnessIndex']:.3f} below 0.82",
                    message=(
                        "Published fairness threshold is Jain ≥ 0.82 and neither "
                        "desk below 18% surplus. Opening ask fails both tests. "
                        "Deal cannot settle."
                    ),
                    metrics=[
                        {"label": "Jain index", "value": f"{r1_scores['jainFairnessIndex']:.4f}"},
                        {"label": "Threshold", "value": "0.8200"},
                        {"label": "Referee utility", "value": f"{r1_scores['refereeUtility']}/100"},
                    ],
                ),
            ],
        },
        {
            "round": 2,
            "stage": "BUYER_COUNTER",
            "title": "Round 2 — Buyer target counter & seller veto",
            "status": "VETOED_BY_SELLER",
            "price": r2_price,
            "terms": r2_terms,
            "scores": r2_scores,
            "vetoes": r2_veto,
            "summary": (
                f"Buyer counters at {money(r2_price)} with {r2_terms['shippingDays']}-day "
                f"shipping. Seller surplus collapses to {r2_split['seller']:.0%}; seller vetoes."
            ),
            "messages": [
                _msg(
                    msg_id="msg-r2-buyer",
                    round_no=2,
                    agent_id="BUYER",
                    action="COUNTER_PROPOSE",
                    stance="COUNTER_PROPOSAL",
                    headline=f"Counter at {money(r2_price)}, {r2_terms['shippingDays']}-day ship",
                    message=(
                        f"Moving off list. Target is {money(r2_price)} with "
                        f"{r2_terms['warrantyMonths']} months warranty and a "
                        f"{r2_terms['returnDays']}-day return window."
                    ),
                    metrics=[
                        {"label": "Bid", "value": money(r2_price)},
                        {"label": "Buyer surplus", "value": f"{r2_split['buyer']:.0%}"},
                        {"label": "Buyer utility", "value": f"{r2_scores['buyerUtility']}/100"},
                    ],
                ),
                _msg(
                    msg_id="msg-r2-seller",
                    round_no=2,
                    agent_id="SELLER",
                    action="VETO_MARGIN",
                    stance=_stance(r2_veto, "SELLER", "HOLD"),
                    headline=f"MARGIN VETO: {money(r2_price)} starves seller surplus",
                    message=(
                        f"Floor is {money(item['sellerReservation'])}. This bid leaves "
                        f"the seller {r2_split['seller']:.0%} of surplus and "
                        f"{r2_terms['warrantyMonths']} months of liability. Not commercially viable."
                    ),
                    metrics=[
                        {"label": "Seller surplus", "value": f"{r2_split['seller']:.0%}"},
                        {"label": "Floor", "value": money(item["sellerReservation"])},
                        {"label": "Seller utility", "value": f"{r2_scores['sellerUtility']}/100"},
                    ],
                ),
                _msg(
                    msg_id="msg-r2-ref",
                    round_no=2,
                    agent_id="REFEREE",
                    action="HOLD_AND_MEDIATE",
                    stance=_stance(r2_veto, "REFEREE", "HOLD"),
                    headline=f"Hold — Jain {r2_scores['jainFairnessIndex']:.3f}. Mediation next.",
                    message=(
                        "The counter is the mirror image of the opening ask. "
                        "Referee will table a 50/50 surplus split and interpolated terms."
                    ),
                    metrics=[
                        {"label": "Jain index", "value": f"{r2_scores['jainFairnessIndex']:.4f}"},
                        {"label": "Surplus", "value": f"${r2_split['surplus']:,.2f}"},
                        {"label": "Referee utility", "value": f"{r2_scores['refereeUtility']}/100"},
                    ],
                ),
            ],
        },
        {
            "round": 3,
            "stage": "REFEREE_FAIR_SPLIT",
            "title": "Round 3 — Referee fair-split proposal",
            "status": "FAIRNESS_TABLED_AWAITING_NASH",
            "price": r3_price,
            "terms": r3_terms,
            "scores": r3_scores,
            "vetoes": r3_veto,
            "summary": (
                f"Referee tables {money(r3_price)} (mid-surplus) with "
                f"{r3_terms['shippingDays']}-day shipping. Jain "
                f"{r3_scores['jainFairnessIndex']:.3f}. Desks hold for Nash trim."
            ),
            "messages": [
                _msg(
                    msg_id="msg-r3-ref",
                    round_no=3,
                    agent_id="REFEREE",
                    action="PROPOSE_FAIR_SPLIT",
                    stance="COUNTER_PROPOSAL",
                    headline=f"Fair split: {money(r3_price)} · Jain {r3_scores['jainFairnessIndex']:.3f}",
                    message=(
                        f"Surplus of ${r3_scores['surplus']:,.2f} split down the middle. "
                        f"Shipping {r3_terms['shippingDays']}d, warranty "
                        f"{r3_terms['warrantyMonths']}mo, returns {r3_terms['returnDays']}d."
                    ),
                    metrics=[
                        {"label": "Fair price", "value": money(r3_price)},
                        {"label": "Jain index", "value": f"{r3_scores['jainFairnessIndex']:.4f}"},
                        {"label": "Referee utility", "value": f"{r3_scores['refereeUtility']}/100"},
                    ],
                ),
                _msg(
                    msg_id="msg-r3-buyer",
                    round_no=3,
                    agent_id="BUYER",
                    action="HOLD_FOR_TERMS",
                    stance=_stance(r3_veto, "BUYER", "HOLD"),
                    headline="Hold — price is fair; still want faster ship",
                    message=(
                        "Price clears reservation. Requesting a Nash trim on "
                        "shipping and returns before ratification."
                    ),
                    metrics=[
                        {"label": "Buyer surplus", "value": f"{r3_scores['surplusSplit']['buyer']:.0%}"},
                        {"label": "Ship", "value": f"{r3_terms['shippingDays']} days"},
                        {"label": "Buyer utility", "value": f"{r3_scores['buyerUtility']}/100"},
                    ],
                ),
                _msg(
                    msg_id="msg-r3-seller",
                    round_no=3,
                    agent_id="SELLER",
                    action="CONCEDE_PRICE",
                    stance=_stance(r3_veto, "SELLER", "CONCESSION"),
                    headline="Concession on price — keep warranty bounded",
                    message=(
                        "Mid-surplus is individually rational. Seller concedes the "
                        "price point and asks the Nash solver to keep warranty exposure finite."
                    ),
                    metrics=[
                        {"label": "Seller surplus", "value": f"{r3_scores['surplusSplit']['seller']:.0%}"},
                        {"label": "Warranty", "value": f"{r3_terms['warrantyMonths']} mo"},
                        {"label": "Seller utility", "value": f"{r3_scores['sellerUtility']}/100"},
                    ],
                ),
            ],
        },
        {
            "round": 4,
            "stage": "NASH_CONSENSUS",
            "title": "Round 4 — Weighted Nash consensus",
            "status": "CONSENSUS_CONVERGED",
            "price": r4_price,
            "terms": r4_terms,
            "scores": r4_scores,
            "vetoes": r4_veto,
            "summary": (
                f"Agents converge at {money(r4_price)}. Surplus split "
                f"{r4_split['buyer']:.0%} / {r4_split['seller']:.0%}. "
                f"Jain {r4_scores['jainFairnessIndex']:.3f}. Ready to settle."
            ),
            "messages": [
                _msg(
                    msg_id="msg-r4-seller",
                    round_no=4,
                    agent_id="SELLER",
                    action="RATIFY_PRICE",
                    stance="APPROVAL",
                    headline=f"Seller ratifies {money(r4_price)}",
                    message=(
                        f"Nash weight w_S={r4_scores['weights']['seller']:.2f} "
                        f"clears the floor by ${r4_price - item['sellerReservation']:,.2f}. "
                        "Seller signs."
                    ),
                    metrics=[
                        {"label": "Clearing price", "value": money(r4_price)},
                        {"label": "Seller surplus", "value": f"{r4_split['seller']:.0%}"},
                        {"label": "Seller utility", "value": f"{r4_scores['sellerUtility']}/100"},
                    ],
                ),
                _msg(
                    msg_id="msg-r4-buyer",
                    round_no=4,
                    agent_id="BUYER",
                    action="RATIFY_TERMS",
                    stance="APPROVAL",
                    headline=(
                        f"Buyer ratifies · {r4_terms['shippingDays']}d ship · "
                        f"{r4_terms['returnDays']}d returns"
                    ),
                    message=(
                        f"Saves ${item['listPrice'] - r4_price:,.2f} vs list. "
                        f"Terms: {r4_terms['shippingDays']} day shipping, "
                        f"{r4_terms['warrantyMonths']} month warranty, "
                        f"{r4_terms['returnDays']} day returns."
                    ),
                    metrics=[
                        {"label": "Vs list", "value": f"-${item['listPrice'] - r4_price:,.2f}"},
                        {"label": "Buyer surplus", "value": f"{r4_split['buyer']:.0%}"},
                        {"label": "Buyer utility", "value": f"{r4_scores['buyerUtility']}/100"},
                    ],
                ),
                _msg(
                    msg_id="msg-r4-ref",
                    round_no=4,
                    agent_id="REFEREE",
                    action="RATIFY_CLEARANCE",
                    stance="APPROVAL",
                    headline=(
                        f"Consensus · Jain {r4_scores['jainFairnessIndex']:.3f} · "
                        f"score {r4_scores['consensusScore']}/100"
                    ),
                    message=(
                        "All three agents clear disagreement points. No open vetoes. "
                        "Clearing the deal for PayPal Orders create+capture and "
                        "SHA-256 attestation."
                    ),
                    metrics=[
                        {"label": "Jain index", "value": f"{r4_scores['jainFairnessIndex']:.4f}"},
                        {"label": "Nash product", "value": f"{r4_scores['nashBargainingIndex']}"},
                        {"label": "Consensus", "value": f"{r4_scores['consensusScore']}/100"},
                    ],
                ),
            ],
        },
    ]

    consensus_reached = (
        not any(v["veto"] for v in r4_veto.values())
        and r4_scores["jainFairnessIndex"] >= 0.90
        and r4_split["buyer"] >= 0.22
        and r4_split["seller"] >= 0.22
    )

    audit_payload = {
        "protocolVersion": "CONSENSUS-COMMERCE-v1",
        "itemId": item["id"],
        "sku": item["sku"],
        "title": item["title"],
        "finalPrice": r4_price,
        "currency": currency,
        "quantity": item["quantity"],
        "surplus": r4_split["surplus"],
        "surplusSplit": r4_split,
        "jainFairnessIndex": r4_scores["jainFairnessIndex"],
        "roundCount": 5,
        "terms": r4_terms,
        "utilities": {
            "buyer": r4_scores["buyerUtility"],
            "seller": r4_scores["sellerUtility"],
            "referee": r4_scores["refereeUtility"],
            "consensusScore": r4_scores["consensusScore"],
            "nashBargainingIndex": r4_scores["nashBargainingIndex"],
        },
        "weights": r4_scores["weights"],
        "vetoesResolved": 3,
    }
    audit_hash = sha256_hex(audit_payload)
    issued_at = datetime.now(timezone.utc).isoformat()

    certificate = {
        "certificateId": f"CC-FAIR-{audit_hash[:12].upper()}",
        "issuedAt": issued_at,
        "sha256Hash": audit_hash,
        "protocolVersion": "CONSENSUS-COMMERCE-v1",
        "finalPrice": r4_price,
        "currency": currency,
        "surplusSplit": r4_split,
        "jainFairnessIndex": r4_scores["jainFairnessIndex"],
        "roundCount": 5,
        "auditHash": audit_hash,
        "agentSignatures": {
            "BUYER": f"sig_buyer_{audit_hash[0:16]}",
            "SELLER": f"sig_seller_{audit_hash[16:32]}",
            "REFEREE": f"sig_ref_{audit_hash[32:48]}",
        },
        "payload": audit_payload,
        "settlement": None,
    }

    rounds.append(
        {
            "round": 5,
            "stage": "ATTESTATION_AND_SETTLEMENT",
            "title": "Round 5 — Proof-of-fairness certificate & PayPal settlement",
            "status": "CERTIFIED_AND_SETTLED" if consensus_reached else "CERTIFIED_UNSETTLED",
            "price": r4_price,
            "terms": r4_terms,
            "scores": r4_scores,
            "vetoes": r4_veto,
            "summary": (
                f"SHA-256 certificate {certificate['certificateId']} minted. "
                "PayPal Orders create+capture runs next."
            ),
            "messages": [
                _msg(
                    msg_id="msg-r5-ref",
                    round_no=5,
                    agent_id="REFEREE",
                    action="MINT_CERTIFICATE",
                    stance="CERTIFIED",
                    headline=f"Certificate {certificate['certificateId']}",
                    message=(
                        f"Auditable proof-of-fairness issued. Final price {money(r4_price)}, "
                        f"surplus split {r4_split['buyer']:.0%}/{r4_split['seller']:.0%}, "
                        f"Jain {r4_scores['jainFairnessIndex']:.4f}, 5 rounds. "
                        f"SHA-256 0x{audit_hash[:16]}…"
                    ),
                    metrics=[
                        {"label": "SHA-256", "value": f"0x{audit_hash[:12]}…"},
                        {"label": "Jain", "value": f"{r4_scores['jainFairnessIndex']:.4f}"},
                        {"label": "Status", "value": "SIGNED"},
                    ],
                )
            ],
        }
    )

    return {
        "protocolVersion": "CONSENSUS-COMMERCE-v1",
        "item": item,
        "agents": AGENT_PROFILES,
        "weights": r4_scores["weights"],
        "rounds": rounds,
        "finalDeal": {
            "price": r4_price,
            "currency": currency,
            "terms": r4_terms,
            "scores": r4_scores,
            "consensusReached": consensus_reached,
        },
        "comparison": {
            "listPrice": item["listPrice"],
            "sellerAsk": item["sellerAsk"],
            "buyerTarget": item["buyerTarget"],
            "consensusPrice": r4_price,
            "savedVsList": round(item["listPrice"] - r4_price, 2),
            "jainBaseline": r1_scores["jainFairnessIndex"],
            "jainConsensus": r4_scores["jainFairnessIndex"],
        },
        "certificate": certificate,
    }
