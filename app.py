"""Consensus Commerce — FastAPI application.

Serves the judge dashboard from /public and the negotiation + PayPal API.
Bind 0.0.0.0:3000 so the Cursor cloud p-3000 URL can reach the demo.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.catalog import CATALOG, get_item
from backend.engine.negotiation import run_negotiation
from backend.paypal.adapter import PayPalAdapter

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"

app = FastAPI(
    title="Consensus Commerce",
    version="1.0.0",
    description="Multi-agent commerce negotiation with PayPal Orders settlement and SHA-256 fairness certificates.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class NegotiateBody(BaseModel):
    itemId: str | None = None
    weights: dict[str, float] | None = None
    settle: bool = True


class WeightsBody(BaseModel):
    buyer: float = Field(ge=0.05, le=0.9)
    seller: float = Field(ge=0.05, le=0.9)
    referee: float = Field(ge=0.05, le=0.9)


def _adapter() -> PayPalAdapter:
    return PayPalAdapter()


def _attach_settlement(result: dict, adapter: PayPalAdapter) -> dict:
    deal = result["finalDeal"]
    if not deal.get("consensusReached"):
        result["settlement"] = None
        result["certificate"]["settlement"] = None
        return result

    amount = f"{deal['price']:.2f}"
    invoice = result["certificate"]["certificateId"]
    settlement = adapter.settle(
        amount=amount,
        currency=deal["currency"],
        description=result["item"]["title"][:127],
        invoice_id=invoice,
    )
    payload = settlement.to_dict()
    # Drop bulky raw PayPal bodies from the dashboard payload; keep them
    # available on GET /api/health-adjacent debug only.
    payload.pop("rawCreate", None)
    payload.pop("rawCapture", None)
    result["settlement"] = payload
    result["certificate"]["settlement"] = {
        "mode": payload["mode"],
        "modeLabel": payload["modeLabel"],
        "orderId": payload["orderId"],
        "status": payload["status"],
        "captureId": payload["captureId"],
        "captureStatus": payload["captureStatus"],
        "note": payload["note"],
    }
    # Append settlement facts to the round-5 transcript.
    r5 = result["rounds"][-1]
    r5["messages"].append(
        {
            "id": "msg-r5-paypal",
            "round": 5,
            "agentId": "REFEREE",
            "action": "SETTLE_PAYPAL",
            "stance": "CERTIFIED",
            "headline": f"PayPal {payload['status']} · {payload['orderId']}",
            "message": (
                f"{payload['modeLabel']}. Intent CAPTURE. Order {payload['orderId']} "
                f"status {payload['status']}"
                + (f", capture {payload['captureId']}" if payload.get("captureId") else "")
                + f". {payload['note']}"
            ),
            "metricsCited": [
                {"label": "Order ID", "value": payload["orderId"]},
                {"label": "Status", "value": payload["status"]},
                {"label": "Mode", "value": payload["mode"]},
            ],
        }
    )
    return result


@app.get("/api/health")
def health() -> dict:
    adapter = _adapter()
    return {
        "status": "ok",
        "service": "Consensus Commerce",
        "protocolVersion": "CONSENSUS-COMMERCE-v1",
        "catalogCount": len(CATALOG),
        "paypal": adapter.status(),
    }


@app.get("/api/catalog")
def catalog() -> dict:
    return {"items": CATALOG, "agents": __import__("backend.engine.agents", fromlist=["AGENT_PROFILES"]).AGENT_PROFILES}


@app.get("/api/paypal/status")
def paypal_status() -> dict:
    return _adapter().status()


@app.post("/api/negotiate")
def negotiate(body: NegotiateBody) -> dict:
    try:
        get_item(body.itemId) if body.itemId else get_item(None)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    result = run_negotiation({"itemId": body.itemId, "weights": body.weights})
    if body.settle:
        result = _attach_settlement(result, _adapter())
    return result


@app.get("/api/negotiate")
def negotiate_get(itemId: str | None = None, settle: bool = True) -> dict:
    try:
        result = run_negotiation({"itemId": itemId})
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if settle:
        result = _attach_settlement(result, _adapter())
    return result


@app.get("/")
def index() -> FileResponse:
    return FileResponse(PUBLIC / "index.html")


if (PUBLIC / "assets").exists():
    app.mount("/assets", StaticFiles(directory=PUBLIC / "assets"), name="assets")


@app.get("/{path:path}")
def static_or_spa(path: str):
    candidate = PUBLIC / path
    if candidate.is_file():
        return FileResponse(candidate)
    raise HTTPException(status_code=404, detail="Not found")


def main() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "3000"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    main()
