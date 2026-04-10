# accounts/paystack.py

"""
accounts/paystack.py

A clean, typed wrapper around the Paystack REST API.
Every method either returns the parsed `data` object on success
or raises PaystackError with a human-readable message.

Design principles:
- No business logic here — just HTTP + error handling.
- All amounts are expected in PESEWAS (GHS subunit × 100).
- Callers (views / tasks) own the Django DB work.
"""

import hashlib
import hmac
import logging
import uuid
from decimal import Decimal
from typing import Optional

import requests
from django.conf import settings

logger = logging.getLogger("accounts.finance")

PAYSTACK_BASE = "https://api.paystack.co"

# Map our internal momo_provider slugs → Paystack bank_code strings
PROVIDER_TO_PAYSTACK: dict[str, str] = {
    "mtn": "MTN",
    "telecel": "VOD",
    "airteltigo": "ATL",
}


class PaystackError(Exception):
    """Raised when the Paystack API returns a non-success response."""

    def __init__(self, message: str, status_code: int = 0, raw: dict = None):
        super().__init__(message)
        self.status_code = status_code
        self.raw = raw or {}


def _headers() -> dict:
    secret = getattr(settings, "PAYSTACK_SECRET_KEY", None)
    if not secret:
        raise PaystackError("PAYSTACK_SECRET_KEY is not configured.")
    return {
        "Authorization": f"Bearer {secret}",
        "Content-Type": "application/json",
    }


def _post(endpoint: str, payload: dict) -> dict:
    """POST to Paystack and return the `data` object."""
    url = f"{PAYSTACK_BASE}{endpoint}"
    try:
        resp = requests.post(url, json=payload, headers=_headers(), timeout=30)
    except requests.RequestException as exc:
        raise PaystackError(f"Network error reaching Paystack: {exc}")

    body = resp.json()
    if not body.get("status"):
        raise PaystackError(
            body.get("message", "Unknown Paystack error"),
            status_code=resp.status_code,
            raw=body,
        )
    return body.get("data", {})


def _get(endpoint: str) -> dict:
    """GET from Paystack and return the `data` object."""
    url = f"{PAYSTACK_BASE}{endpoint}"
    try:
        resp = requests.get(url, headers=_headers(), timeout=30)
    except requests.RequestException as exc:
        raise PaystackError(f"Network error reaching Paystack: {exc}")

    body = resp.json()
    if not body.get("status"):
        raise PaystackError(
            body.get("message", "Unknown Paystack error"),
            status_code=resp.status_code,
            raw=body,
        )
    return body.get("data", {})


# ---------------------------------------------------------------------------
# Transactions (Money In)
# ---------------------------------------------------------------------------

def initialize_transaction(
    *,
    email: str,
    amount_ghs: Decimal,
    reference: str,
    callback_url: str = None,
    metadata: dict = None,
    channels: list[str] = None,
) -> dict:
    """
    Initialize a Paystack transaction.

    Returns:
        {
            "authorization_url": "https://checkout.paystack.com/...",
            "access_code": "...",
            "reference": "..."
        }
    """
    # Paystack requires amount in pesewas (GHS subunit × 100)
    from decimal import ROUND_HALF_UP
    amount_pesewas = int(
        (amount_ghs * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )

    payload: dict = {
        "email": email,
        "amount": amount_pesewas,
        "reference": reference,
        "currency": "GHS",
        # Default to mobile_money + card; caller can override
        "channels": channels or ["mobile_money", "card"],
    }
    if callback_url:
        payload["callback_url"] = callback_url
    if metadata:
        payload["metadata"] = metadata

    data = _post("/transaction/initialize", payload)
    logger.info(
        {
            "event": "paystack_transaction_initialized",
            "reference": reference,
            "amount_ghs": str(amount_ghs),
        }
    )
    return data


def verify_transaction(reference: str) -> dict:
    """
    Verify a transaction by reference.

    Returns the full Paystack transaction object.
    Raises PaystackError if the transaction is not found or not successful.
    """
    data = _get(f"/transaction/verify/{reference}")

    if data.get("status") != "success":
        raise PaystackError(
            f"Transaction {reference} is not successful. "
            f"Status: {data.get('status')}. "
            f"Gateway: {data.get('gateway_response')}",
            raw=data,
        )
    return data


# ---------------------------------------------------------------------------
# Transfer Recipients (Money Out — prerequisite)
# ---------------------------------------------------------------------------

def create_transfer_recipient(
    *,
    name: str,
    account_number: str,
    momo_provider: str,
) -> str:
    """
    Create a Paystack Transfer Recipient for a Ghanaian MoMo account.

    Args:
        name:           Full name on the MoMo account.
        account_number: Phone number (E.164 or local format).
        momo_provider:  One of 'mtn', 'telecel', 'airteltigo'.

    Returns:
        recipient_code  e.g. "RCP_xxxxxxxxxxxx"
    """
    bank_code = PROVIDER_TO_PAYSTACK.get(momo_provider.lower())
    if not bank_code:
        raise PaystackError(
            f"Unsupported MoMo provider: {momo_provider}. "
            f"Must be one of: {list(PROVIDER_TO_PAYSTACK.keys())}"
        )

    # Strip + prefix — Paystack expects local format for GHS mobile_money
    clean_number = account_number.lstrip("+")

    payload = {
        "type": "mobile_money",
        "name": name,
        "account_number": clean_number,
        "bank_code": bank_code,
        "currency": "GHS",
    }

    data = _post("/transferrecipient", payload)
    recipient_code = data.get("recipient_code")
    if not recipient_code:
        raise PaystackError("Paystack did not return a recipient_code.", raw=data)

    logger.info(
        {
            "event": "paystack_recipient_created",
            "recipient_code": recipient_code,
            "provider": momo_provider,
        }
    )
    return recipient_code


# ---------------------------------------------------------------------------
# Transfers (Money Out)
# ---------------------------------------------------------------------------

def initiate_transfer(
    *,
    recipient_code: str,
    amount_ghs: Decimal,
    reference: str,
    reason: str = "SnappX Payout",
) -> dict:
    """
    Initiate a transfer to a previously created recipient.

    Args:
        recipient_code: Paystack RCP_xxx code.
        amount_ghs:     Amount in GHS (will be converted to pesewas).
        reference:      Your unique transfer reference (16-50 chars,
                        lowercase alphanumeric + dash/underscore).
        reason:         Narration shown on recipient's statement.

    Returns:
        Paystack transfer data dict (includes transfer_code, status).
    """
    from decimal import ROUND_HALF_UP
    amount_pesewas = int(
        (amount_ghs * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )

    payload = {
        "source": "balance",
        "amount": amount_pesewas,
        "recipient": recipient_code,
        "reference": reference,
        "reason": reason,
        "currency": "GHS",
    }

    data = _post("/transfer", payload)
    logger.info(
        {
            "event": "paystack_transfer_initiated",
            "transfer_code": data.get("transfer_code"),
            "reference": reference,
            "amount_ghs": str(amount_ghs),
            "status": data.get("status"),
        }
    )
    return data


def verify_transfer(reference: str) -> dict:
    """
    Verify the current status of a transfer by reference.
    Useful for polling when webhooks aren't received.
    """
    return _get(f"/transfer/verify/{reference}")


# ---------------------------------------------------------------------------
# Webhook signature verification
# ---------------------------------------------------------------------------

def verify_webhook_signature(payload_bytes: bytes, signature_header: str) -> bool:
    """
    Verify that a webhook request originated from Paystack.

    Paystack signs the raw request body with your secret key using
    HMAC-SHA512 and sends the hex digest in the x-paystack-signature header.

    Args:
        payload_bytes:      Raw request body bytes (before any JSON parsing).
        signature_header:   Value of the 'x-paystack-signature' header.

    Returns:
        True if the signature matches, False otherwise.
    """
    secret = getattr(settings, "PAYSTACK_SECRET_KEY", "")
    if not secret:
        logger.error("PAYSTACK_SECRET_KEY not set — cannot verify webhook.")
        return False

    # Guard against None/empty signature before compare_digest
    if not signature_header:
        return False

    expected = hmac.HMAC(
        secret.encode("utf-8"),
        payload_bytes,
        hashlib.sha512,
    ).hexdigest()

    # expected = hmac.new(
    #     secret.encode("utf-8"),
    #     payload_bytes,
    #     hashlib.sha512,
    # ).hexdigest()

    return hmac.compare_digest(expected, signature_header)


# ---------------------------------------------------------------------------
# Reference generation helpers
# ---------------------------------------------------------------------------

def generate_topup_reference(user_id: int) -> str:
    """Generate a unique reference for a wallet top-up."""
    short_uuid = uuid.uuid4().hex[:16]
    return f"snx-topup-{user_id}-{short_uuid}"


def generate_payout_reference(group_id: int, cycle: int, attempt: int = 1) -> str:
    """
    Generate a unique, Paystack-safe reference for a group payout transfer.

    Paystack constraints:
      - Lowercase letters (a-z), digits (0-9), hyphen (-), underscore (_) only.
      - Minimum 16 characters, maximum 50 characters.

    Format: snx-payout-{group_id}-c{cycle}-a{attempt}-{uuid12}

    Max length breakdown (worst case):
      "snx-payout-" = 11
      group_id (9 digits max) = 9
      "-c" = 2
      cycle (4 digits max) = 4
      "-a" = 2
      attempt (1 digit) = 1
      "-" = 1
      uuid hex (12 chars) = 12
      Total = 42 chars — safely under the 50-char limit.

    Previously used 16 hex chars which, combined with the "-retry" suffix appended
    in retry_failed_payouts, could produce strings of 51+ characters, violating
    the Paystack maximum length requirement.
    """
    short_uuid = uuid.uuid4().hex[:12]
    return f"snx-payout-{group_id}-c{cycle}-a{attempt}-{short_uuid}"


def generate_cashout_reference(user_id: int) -> str:
    """Generate a unique reference for a user-initiated cash-out."""
    short_uuid = uuid.uuid4().hex[:16]
    return f"snx-cashout-{user_id}-{short_uuid}"
