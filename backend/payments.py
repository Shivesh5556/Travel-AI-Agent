"""
Razorpay checkout, TEST MODE only.

Nothing here can move real money. Test keys start with rzp_test_ and Razorpay
publishes dummy card numbers specifically so students can build this.

We talk to their REST API with httpx and HTTP Basic auth instead of using the
SDK, because it is two functions and this way you can see what is happening.

Security note worth saying out loud in a viva: this server never sees a card
number. Razorpay's hosted widget collects that inside their own iframe. We
only ever handle an order id going out and a signed payment id coming back,
and we verify that signature ourselves below.
"""

import hashlib
import hmac

import httpx

import config

RAZORPAY_ORDERS_URL = "https://api.razorpay.com/v1/orders"


class PaymentsNotConfigured(Exception):
    pass


async def create_order(amount_inr: int, receipt: str) -> dict:
    """
    Create an order. Razorpay works in paise, so we multiply by 100.
    Returns the order dict, whose "id" the frontend hands to the widget.
    """
    if not config.PAYMENTS_ENABLED:
        raise PaymentsNotConfigured(
            "Add RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET to .env to enable checkout")

    body = {
        "amount": int(amount_inr) * 100,
        "currency": "INR",
        "receipt": receipt[:40],
        "notes": {"project": "Ghumi Ghumi", "mode": "test"},
    }

    async with httpx.AsyncClient(timeout=config.HTTP_TIMEOUT) as client:
        response = await client.post(
            RAZORPAY_ORDERS_URL,
            json=body,
            auth=(config.RAZORPAY_KEY_ID, config.RAZORPAY_KEY_SECRET),
        )

    if response.status_code not in (200, 201):
        raise PaymentsNotConfigured(f"Razorpay said {response.status_code}: {response.text[:200]}")

    return response.json()


def verify_signature(order_id: str, payment_id: str, signature: str) -> bool:
    """
    Check that the payment really came from Razorpay and was not faked by
    somebody calling our endpoint directly.

    Razorpay signs "order_id|payment_id" with our secret using HMAC-SHA256.
    We do the same and compare. hmac.compare_digest is used instead of == so
    the comparison takes the same time regardless of where it differs, which
    prevents a timing attack.
    """
    if not config.PAYMENTS_ENABLED:
        return False
    if not (order_id and payment_id and signature):
        return False

    expected = hmac.new(
        config.RAZORPAY_KEY_SECRET.encode(),
        f"{order_id}|{payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


def make_pnr(trip_id: str, payment_id: str) -> str:
    """
    A booking reference. Deterministic, so the same payment always produces
    the same code. Format looks like GG-4F2A9C.
    """
    digest = hashlib.sha256(f"{trip_id}{payment_id}".encode()).hexdigest()
    return "GG-" + digest[:6].upper()
