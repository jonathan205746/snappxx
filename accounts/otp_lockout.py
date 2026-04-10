# accounts/otp_lockout.py

"""

What it does:
    Tracks failed OTP attempts per phone number using Django's cache backend.
    After MAX_OTP_FAILURES failures, the phone is locked out for LOCKOUT_SECONDS.

    Phone numbers are never stored raw in cache keys — they're hashed first.
    This means even if someone dumps your cache, they can't extract phone numbers.

How the cache backend is configured:
    See CACHES in core/settings.py.

    Development:  LocMemCache (in-process, lost on restart — fine for dev)
    Production:   MUST be Redis (LocMemCache is per-process and Vercel
                  spins up a new process per request, so lockout state
                  is lost between requests).

    To use Upstash Redis on Vercel:
        pip install django-redis
        Then in settings.py:
            CACHES = {
                "default": {
                    "BACKEND": "django_redis.cache.RedisCache",
                    "LOCATION": config("UPSTASH_REDIS_URL"),
                    "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
                }
            }

Usage in VerifyOTPView (already integrated in views_otp_section.py):
    from .otp_lockout import check_lockout, record_failure, clear_failures

    if check_lockout(phone):
        return Response({"error": "Too many failed attempts..."}, status=429)

    if verify_and_invalidate_otp_sync(phone, code):
        clear_failures(phone)
        # success path
    else:
        count = record_failure(phone)
        # failure path
"""

import hashlib
from django.core.cache import cache

# Configuration

# Lock the phone after this many consecutive failures
MAX_OTP_FAILURES = 3

# Keep the lockout for this many seconds (3600 = 1 hour)
LOCKOUT_SECONDS = 3600


# Internal helpers

def _lockout_key(phone: str) -> str:
    """
    Build a cache key for this phone number.

    We hash the phone so that:
      - Cache keys are a fixed length (no risk of very long keys)
      - Raw phone numbers never appear in cache storage
      - Two identical phones always produce the same key (deterministic)

    We use only the first 20 hex chars of SHA-256 — that's 80 bits of
    entropy, far more than needed to uniquely identify a phone number.
    """
    h = hashlib.sha256(phone.encode("utf-8")).hexdigest()[:20]
    return f"otp_failures:{h}"


# Public API

def check_lockout(phone: str) -> bool:
    """
    Return True if this phone number is currently locked out.

    Call this BEFORE attempting OTP verification.
    If it returns True, reject the request immediately with HTTP 429.
    """
    return int(cache.get(_lockout_key(phone), 0)) >= MAX_OTP_FAILURES


def record_failure(phone: str) -> int:
    """
    Record a failed OTP attempt for this phone number.
    Returns the new failure count.

    Call this AFTER a failed OTP verification.
    The lockout timer resets on every failure (sliding window).
    """
    key = _lockout_key(phone)
    try:
        # incr() is atomic — safe under concurrent requests
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

    Call this AFTER a successful OTP verification.
    This prevents legitimate users from getting locked out after a previous
    failed attempt — the counter resets cleanly on success.
    """
    cache.delete(_lockout_key(phone))
