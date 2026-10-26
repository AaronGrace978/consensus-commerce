"""PayPal Orders v2 adapter.

Reads PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET from the environment.
Never hardcodes secrets.

- Both credentials present → live sandbox: OAuth → POST /v2/checkout/orders
  (intent CAPTURE) → POST /v2/checkout/orders/{id}/capture.
- Either credential missing → SANDBOX SIMULATION with the identical
  response shape so the dashboard and certificate stay unchanged.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import asdict, dataclass
from typing import Any

import httpx

DEFAULT_SANDBOX_BASE = "https://api-m.sandbox.paypal.com"


@dataclass
class SettlementResult:
    mode: str
    modeLabel: str
    orderId: str
    status: str
    captureId: str | None
    captureStatus: str | None
    intent: str
    amount: str
    currency: str
    description: str
    invoiceId: str
    approveUrl: str | None
    requestId: str
    note: str
    rawCreate: dict[str, Any] | None
    rawCapture: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PayPalAdapter:
    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        api_base: str | None = None,
    ) -> None:
        self.client_id = (client_id if client_id is not None else os.environ.get("PAYPAL_CLIENT_ID", "")).strip()
        self.client_secret = (
            client_secret if client_secret is not None else os.environ.get("PAYPAL_CLIENT_SECRET", "")
        ).strip()
        self.api_base = (
            api_base if api_base is not None else os.environ.get("PAYPAL_API_BASE", DEFAULT_SANDBOX_BASE)
        ).rstrip("/")
        self.mode = "paypal_sandbox" if (self.client_id and self.client_secret) else "sandbox_simulation"

    @property
    def mode_label(self) -> str:
        if self.mode == "paypal_sandbox":
            return "PAYPAL SANDBOX (live Orders API)"
        return "SANDBOX SIMULATION (no credentials)"

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "modeLabel": self.mode_label,
            "hasClientId": bool(self.client_id),
            "hasClientSecret": bool(self.client_secret),
            "apiBase": self.api_base,
            "credentialsRequired": ["PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET"],
        }

    def settle(
        self,
        *,
        amount: str,
        currency: str = "USD",
        description: str,
        invoice_id: str,
        timeout: float = 20.0,
    ) -> SettlementResult:
        if self.mode == "sandbox_simulation":
            return self._simulate(amount=amount, currency=currency, description=description, invoice_id=invoice_id)
        return self._live(
            amount=amount,
            currency=currency,
            description=description,
            invoice_id=invoice_id,
            timeout=timeout,
        )

    def _simulate(self, *, amount: str, currency: str, description: str, invoice_id: str) -> SettlementResult:
        seed = hashlib.sha256(f"{invoice_id}|{amount}|{currency}".encode("utf-8")).hexdigest().upper()
        order_id = f"SIM{seed[:14]}"
        capture_id = f"CAP{seed[14:28]}"
        request_id = f"sim-{seed[:12].lower()}"
        return SettlementResult(
            mode=self.mode,
            modeLabel=self.mode_label,
            orderId=order_id,
            status="COMPLETED",
            captureId=capture_id,
            captureStatus="COMPLETED",
            intent="CAPTURE",
            amount=amount,
            currency=currency,
            description=description,
            invoiceId=invoice_id,
            approveUrl=None,
            requestId=request_id,
            note=(
                "SANDBOX SIMULATION — PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET "
                "are not set. Flow shape matches Orders v2 create+capture."
            ),
            rawCreate={
                "id": order_id,
                "status": "CREATED",
                "intent": "CAPTURE",
                "purchase_units": [
                    {
                        "reference_id": invoice_id,
                        "description": description,
                        "amount": {"currency_code": currency, "value": amount},
                    }
                ],
            },
            rawCapture={
                "id": order_id,
                "status": "COMPLETED",
                "purchase_units": [
                    {
                        "payments": {
                            "captures": [{"id": capture_id, "status": "COMPLETED", "amount": {"currency_code": currency, "value": amount}}]
                        }
                    }
                ],
            },
        )

    def _access_token(self, client: httpx.Client) -> str:
        response = client.post(
            f"{self.api_base}/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(self.client_id, self.client_secret),
            headers={"Accept": "application/json", "Accept-Language": "en_US"},
        )
        response.raise_for_status()
        body = response.json()
        token = body.get("access_token")
        if not token:
            raise RuntimeError("PayPal OAuth response missing access_token")
        return token

    def _live(
        self,
        *,
        amount: str,
        currency: str,
        description: str,
        invoice_id: str,
        timeout: float,
    ) -> SettlementResult:
        request_id = str(uuid.uuid4())
        create_body = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": invoice_id[:127],
                    "description": description[:127],
                    "custom_id": invoice_id[:127],
                    "invoice_id": invoice_id[:127],
                    "amount": {"currency_code": currency, "value": amount},
                }
            ],
        }

        with httpx.Client(timeout=timeout) as client:
            token = self._access_token(client)
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "PayPal-Request-Id": request_id,
                "Prefer": "return=representation",
            }
            created = client.post(
                f"{self.api_base}/v2/checkout/orders",
                headers=headers,
                json=create_body,
            )
            created.raise_for_status()
            create_json = created.json()
            order_id = create_json.get("id", "")
            status = create_json.get("status", "CREATED")
            approve = None
            for link in create_json.get("links") or []:
                if link.get("rel") == "approve":
                    approve = link.get("href")
                    break

            capture_json = None
            capture_id = None
            capture_status = None
            note = "PayPal sandbox order created."
            try:
                captured = client.post(
                    f"{self.api_base}/v2/checkout/orders/{order_id}/capture",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                        "PayPal-Request-Id": f"{request_id}-cap",
                        "Prefer": "return=representation",
                    },
                    json={},
                )
                capture_json = captured.json()
                if captured.is_success:
                    status = capture_json.get("status", status)
                    capture_status = status
                    units = capture_json.get("purchase_units") or []
                    if units:
                        captures = ((units[0].get("payments") or {}).get("captures")) or []
                        if captures:
                            capture_id = captures[0].get("id")
                            capture_status = captures[0].get("status", capture_status)
                    note = "PayPal sandbox order created and captured."
                else:
                    # Typical sandbox outcome without a payer approval:
                    # ORDER_NOT_APPROVED. Surface the created order honestly.
                    issue = ""
                    details = capture_json.get("details") or []
                    if details:
                        issue = details[0].get("issue") or details[0].get("description") or ""
                    note = (
                        f"PayPal sandbox order {order_id} created ({status}). "
                        f"Capture not completed"
                        + (f" ({issue})" if issue else "")
                        + ". Payer approval is required on the approve URL, "
                        "or a payment_source on capture."
                    )
            except httpx.HTTPError as exc:
                note = (
                    f"PayPal sandbox order {order_id} created ({status}). "
                    f"Capture request failed: {exc}"
                )

        return SettlementResult(
            mode=self.mode,
            modeLabel=self.mode_label,
            orderId=order_id,
            status=status,
            captureId=capture_id,
            captureStatus=capture_status,
            intent="CAPTURE",
            amount=amount,
            currency=currency,
            description=description,
            invoiceId=invoice_id,
            approveUrl=approve,
            requestId=request_id,
            note=note,
            rawCreate=create_json,
            rawCapture=capture_json,
        )


def get_adapter() -> PayPalAdapter:
    return PayPalAdapter()
