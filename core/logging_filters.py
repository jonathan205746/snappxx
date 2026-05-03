# core/logging_filters.py

import re
import logging


_PHONE_RE = re.compile(r"\b(0[235]\d{8})\b")

_E164_RE = re.compile(r"\+233\d{9}")

# Email addresses
_EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b")

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

    text = _PHONE_RE.sub(lambda m: f"***{m.group(1)[-4:]}", text)

    text = _E164_RE.sub(lambda m: f"+233***{m.group(0)[-4:]}", text)

    text = _EMAIL_RE.sub("[email]", text)

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
        if isinstance(record.msg, dict):
            record.msg = {
                k: _scrub(str(v)) if isinstance(v, str) else v
                for k, v in record.msg.items()
            }
        elif isinstance(record.msg, str):
            record.msg = _scrub(record.msg)

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
