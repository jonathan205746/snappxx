import hashlib
from django.core.cache import cache

# Lock the phone after this many consecutive failures
MAX_OTP_FAILURES = 3

# Keep the lockout for this many seconds (3600 = 1 hour)
LOCKOUT_SECONDS = 3600


# Internal helpers

def _lockout_key(phone: str) -> str:
    """
    Build a cache key for this phone number.

    Hash the phone so that:
      - Cache keys are a fixed length (no risk of very long keys)
      - Raw phone numbers never appear in cache storage
      - Two identical phones always produce the same key (deterministic)
    """
    h = hashlib.sha256(phone.encode("utf-8")).hexdigest()[:20]
    return f"otp_failures:{h}"


# Public API

def check_lockout(phone: str) -> bool:
    """
    Return True if this phone number is currently locked out.
    """
    return int(cache.get(_lockout_key(phone), 0)) >= MAX_OTP_FAILURES


def record_failure(phone: str) -> int:
    """
    Record a failed OTP attempt for this phone number.
    Returns the new failure count.
    """
    key = _lockout_key(phone)
    try:
        new_count = cache.incr(key)
        # Reset the expiry on every failure so the window slides
        cache.expire(key, LOCKOUT_SECONDS)
        return new_count
    except ValueError:
        # Key doesn't exist yet (first failure)
        cache.set(key, 1, timeout=LOCKOUT_SECONDS)
        return 1


def clear_failures(phone: str) -> None:
    """
    Clear the failure counter for this phone number.
    """
    cache.delete(_lockout_key(phone))
