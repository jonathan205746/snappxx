# core/logging_filters.py

import re
import logging

# Regex patterns

# Ghanaian mobile numbers in local format: 024, 025, 050, 053, 055, 059...
_PHONE_RE = re.compile(r"\b(0[235]\d{8})\b")

# E.164 format: +233xxxxxxxxx
_E164_RE = re.compile(r"\+233\d{9}")

# Email addresses
_EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b")

# Paystack internal references (our format from paystack.py helpers)
_PAYSTACK_REF_RE = re.compile(
    r"\bsnx-(topup|payout|cashout)-[a-z0-9\-]{10,}\b", re.I
)


def _scrub(text: str) -> str:
    """
    Return a copy of text with PII replaced.
    Phone numbers keep their last 4 digits for traceability.
    """
    if not text:
        return text

    # Keep last 4 digits of local phones: 0244123456 → ***3456
    text = _PHONE_RE.sub(lambda m: f"***{m.group(1)[-4:]}", text)

    # E.164 phones: +233244123456 → +233***3456
    text = _E164_RE.sub(lambda m: f"+233***{m.group(0)[-4:]}", text)

    # Emails: user@example.com → [email]
    text = _EMAIL_RE.sub("[email]", text)

    # Paystack refs: snx-payout-7-c1-a1-abc123def456 → [ref]
    text = _PAYSTACK_REF_RE.sub("[ref]", text)

    return text


class PIIScrubberFilter(logging.Filter):
    """
    Logging filter that scrubs PII before log records are emitted.

    Handles three formats your codebase uses:
      1. String messages:   logger.error("OTP failed for 0244123456")
      2. Dict messages:     logger.info({"phone": "0244123456", "event": "..."})
         (structured logging pattern used throughout accounts/tasks.py)
      3. Positional args:   logger.error("failed: %s", phone_number)
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # Format 1 & 2: record.msg
        if isinstance(record.msg, dict):
            record.msg = {
                k: _scrub(str(v)) if isinstance(v, str) else v
                for k, v in record.msg.items()
            }
        elif isinstance(record.msg, str):
            record.msg = _scrub(record.msg)

        # Format 3: record.args
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: _scrub(str(v)) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    _scrub(str(a)) if isinstance(a, str) else a
                    for a in record.args
                )

        return True
