"""Verification suite for Consensus Commerce negotiation + PayPal adapter."""

from __future__ import annotations

import math

import pytest

from backend.catalog import CATALOG, get_item
from backend.engine.fairness import jain_index, nash_price, sha256_hex, should_veto, surplus_split
from backend.engine.negotiation import run_negotiation
from backend.paypal.adapter import PayPalAdapter


def test_jain_equal_is_one():
    assert math.isclose(jain_index([1, 1]), 1.0)
    assert math.isclose(jain_index([40, 40, 40]), 1.0)


def test_jain_unequal_is_lower():
    fair = jain_index([0.5, 0.5])
    unfair = jain_index([0.9, 0.1])
    assert fair > unfair
    assert unfair < 0.82


def test_surplus_split_midpoint():
    split = surplus_split(125, 100, 150)
    assert split["surplus"] == 50
    assert math.isclose(split["buyer"], 0.5)
    assert math.isclose(split["seller"], 0.5)


def test_nash_equal_weights_is_midpoint():
    assert nash_price(100, 200, 0.5, 0.5) == 150


def test_veto_buyer_above_reservation():
    vetoed, reason = should_veto(
        agent_id="BUYER",
        price=260,
        seller_reservation=100,
        buyer_reservation=200,
        jain=0.5,
    )
    assert vetoed
    assert reason == "PRICE_ABOVE_RESERVATION"


def test_veto_referee_low_jain():
    vetoed, reason = should_veto(
        agent_id="REFEREE",
        price=195,
        seller_reservation=100,
        buyer_reservation=200,
        jain=0.60,
    )
    assert vetoed
    assert reason == "JAIN_BELOW_THRESHOLD"


def test_every_catalog_item_reaches_consensus():
    for item in CATALOG:
        result = run_negotiation({"itemId": item["id"]})
        assert len(result["rounds"]) == 5
        assert result["finalDeal"]["consensusReached"] is True
        assert result["certificate"]["jainFairnessIndex"] >= 0.90
        assert result["finalDeal"]["price"] >= item["sellerReservation"]
        assert result["finalDeal"]["price"] <= item["buyerReservation"]
        r1 = result["rounds"][0]
        assert r1["messages"][1]["stance"] == "VETO"
        assert r1["messages"][2]["stance"] == "VETO"
        r4 = result["rounds"][3]
        assert r4["status"] == "CONSENSUS_CONVERGED"
        assert all(m["stance"] == "APPROVAL" for m in r4["messages"])


def test_certificate_hash_is_deterministic():
    a = run_negotiation({"itemId": "leica-m6"})
    b = run_negotiation({"itemId": "leica-m6"})
    assert a["certificate"]["sha256Hash"] == b["certificate"]["sha256Hash"]
    assert len(a["certificate"]["sha256Hash"]) == 64
    assert a["certificate"]["sha256Hash"] == sha256_hex(a["certificate"]["payload"])


def test_unknown_item_raises():
    with pytest.raises(KeyError):
        get_item("no-such-sku")


def test_paypal_simulation_mode_without_credentials(monkeypatch):
    monkeypatch.delenv("PAYPAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("PAYPAL_CLIENT_SECRET", raising=False)
    adapter = PayPalAdapter(client_id="", client_secret="")
    assert adapter.mode == "sandbox_simulation"
    result = adapter.settle(
        amount="2325.00",
        currency="USD",
        description="Leica M6 Titan — 1992, CLA'd",
        invoice_id="CC-FAIR-TEST",
    )
    assert result.mode == "sandbox_simulation"
    assert result.orderId.startswith("SIM")
    assert result.status == "COMPLETED"
    assert result.captureId.startswith("CAP")
    assert result.intent == "CAPTURE"
    assert "SANDBOX SIMULATION" in result.note
    # Same invoice + amount is deterministic.
    again = adapter.settle(
        amount="2325.00",
        currency="USD",
        description="Leica M6 Titan — 1992, CLA'd",
        invoice_id="CC-FAIR-TEST",
    )
    assert again.orderId == result.orderId


def test_paypal_live_mode_flag_with_credentials():
    adapter = PayPalAdapter(client_id="sandbox-client", client_secret="sandbox-secret")
    assert adapter.mode == "paypal_sandbox"
    assert "PAYPAL SANDBOX" in adapter.mode_label
