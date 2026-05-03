import logging
from typing import Optional

import requests
from django.conf import settings

logger = logging.getLogger("accounts.finance")

TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}"


class TelegramError(Exception):
    """Raised when the Telegram API returns a non-OK response."""

    def __init__(self, message: str, error_code: int = 0, raw: dict = None):
        super().__init__(message)
        self.error_code = error_code
        self.raw = raw or {}


def _token() -> str:
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", None)
    if not token:
        raise TelegramError("TELEGRAM_BOT_TOKEN is not configured.")
    return token


def _base_url() -> str:
    return TELEGRAM_API_BASE.format(token=_token())


def _post(method: str, payload: dict) -> dict:
    """POST to a Telegram Bot API method and return the result object."""
    url = f"{_base_url()}/{method}"
    try:
        resp = requests.post(url, json=payload, timeout=15)
    except requests.RequestException as exc:
        raise TelegramError(f"Network error reaching Telegram: {exc}")

    body = resp.json()
    if not body.get("ok"):
        raise TelegramError(
            body.get("description", "Unknown Telegram error"),
            error_code=body.get("error_code", 0),
            raw=body,
        )
    return body.get("result", {})


def _get(method: str, params: dict = None) -> dict:
    """GET a Telegram Bot API method and return the result object."""
    url = f"{_base_url()}/{method}"
    try:
        resp = requests.get(url, params=params or {}, timeout=15)
    except requests.RequestException as exc:
        raise TelegramError(f"Network error reaching Telegram: {exc}")

    body = resp.json()
    if not body.get("ok"):
        raise TelegramError(
            body.get("description", "Unknown Telegram error"),
            error_code=body.get("error_code", 0),
            raw=body,
        )
    return body.get("result", {})


def send_message(
    chat_id: str | int,
    text: str,
    parse_mode: str = "HTML",
    disable_web_page_preview: bool = True,
) -> dict:
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_web_page_preview,
    }
    result = _post("sendMessage", payload)
    logger.info({
        "event": "telegram_message_sent",
        "chat_id": chat_id,
        "text_length": len(text),
    })
    return result


def pin_message(chat_id: str | int, message_id: int) -> dict:
    """Pin a message in a group chat."""
    return _post("pinChatMessage", {
        "chat_id": chat_id,
        "message_id": message_id,
        "disable_notification": True,
    })


def get_chat(chat_id: str | int) -> dict:
    """
    Fetch metadata about a chat. Useful for verifying the bot is still
    a member of a group before attempting to send messages.
    """
    return _get("getChat", {"chat_id": chat_id})


def set_webhook(webhook_url: str, secret_token: str = None) -> dict:
    """
    Register a webhook URL with Telegram.
    """
    payload: dict = {"url": webhook_url}
    if secret_token:
        payload["secret_token"] = secret_token
    return _post("setWebhook", payload)


def delete_webhook() -> dict:
    """Remove the registered webhook (reverts to polling mode)."""
    return _post("deleteWebhook", {})


def get_webhook_info() -> dict:
    """Return current webhook configuration."""
    return _get("getWebhookInfo")


def get_me() -> dict:
    """Return basic info about the bot itself. Good for health checks."""
    return _get("getMe")


def fmt_contribution_reminder(
    group_name: str,
    amount: str,
    cycle: int,
    days_left: int,
    dashboard_url: str,
) -> str:
    urgency = "⏰" if days_left > 1 else "🚨"
    due_text = f"in {days_left} day{'s' if days_left != 1 else ''}" if days_left > 0 else "TODAY"
    return (
        f"{urgency} <b>Contribution Reminder — {group_name}</b>\n\n"
        f"Cycle <b>{cycle}</b> contribution of <b>₵{amount}</b> is due <b>{due_text}</b>.\n\n"
        f"Members who haven't contributed yet, please log in and contribute:\n"
        f"👉 {dashboard_url}"
    )


def fmt_payout_announcement(
    group_name: str,
    beneficiary_name: str,
    amount: str,
    cycle: int,
) -> str:
    return (
        f"🎉 <b>Payout — {group_name}</b>\n\n"
        f"Congratulations to <b>{beneficiary_name}</b>!\n"
        f"Cycle <b>{cycle}</b> payout of <b>₵{amount}</b> has been processed "
        f"and will arrive on their MoMo shortly. 💰"
    )


def fmt_group_activated(group_name: str, member_count: int, dashboard_url: str) -> str:
    return (
        f"🚀 <b>{group_name} is now LIVE!</b>\n\n"
        f"All <b>{member_count}</b> members have joined. "
        f"Payout cycles have officially started!\n\n"
        f"Track contributions and payouts:\n"
        f"👉 {dashboard_url}"
    )


def fmt_new_member(group_name: str, member_name: str, current: int, expected: int) -> str:
    spots_left = expected - current
    if spots_left > 0:
        progress = f"{current}/{expected} members — {spots_left} spot{'s' if spots_left != 1 else ''} left"
    else:
        progress = f"Group is now full! 🎉"
    return (
        f"👋 <b>New Member — {group_name}</b>\n\n"
        f"<b>{member_name}</b> has joined the group.\n"
        f"{progress}"
    )


def fmt_contribution_update(
    group_name: str,
    cycle: int,
    contributed_count: int,
    total_members: int,
    total_collected: str,
    pot_target: str,
) -> str:
    percent = int((contributed_count / total_members) * 100) if total_members else 0
    bar_filled = int(percent / 10)
    bar = "█" * bar_filled + "░" * (10 - bar_filled)
    return (
        f"📊 <b>Contribution Update — {group_name}</b>\n\n"
        f"Cycle <b>{cycle}</b> progress:\n"
        f"<code>[{bar}] {percent}%</code>\n\n"
        f"<b>{contributed_count}/{total_members}</b> members contributed\n"
        f"Collected: <b>₵{total_collected}</b> / ₵{pot_target}"
    )


def fmt_welcome(group_name: str, platform: str, dashboard_url: str) -> str:
    return (
        f"👋 <b>SnappX Bot is now connected to {group_name}!</b>\n\n"
        f"I'll send this group:\n"
        f"  • Contribution reminders\n"
        f"  • Payout announcements\n"
        f"  • New member alerts\n"
        f"  • Cycle progress updates\n\n"
        f"Manage your group on SnappX:\n"
        f"👉 {dashboard_url}"
    )
