import logging
from decimal import Decimal

from django.conf import settings
from django_qstash import shared_task

from .telegram import (
    TelegramError,
    fmt_contribution_reminder,
    fmt_contribution_update,
    fmt_group_activated,
    fmt_new_member,
    fmt_payout_announcement,
    fmt_welcome,
    send_message,
)

logger = logging.getLogger("accounts.finance")

# Convenience: build the dashboard URL for a group
def _group_url(public_id: str) -> str:
    frontend_url = getattr(settings, "FRONTEND_URL", "https://www.snappx.app")
    return f"{frontend_url}/dashboard?page=Groups&group={public_id}"


def _get_telegram_chat_id(group_id: int) -> str | None:
    """
    Return the Telegram chat_id for a group, or None if not linked.
    Imported inside the function to avoid circular imports at module load.
    """
    from .models import BotGroupLink
    link = BotGroupLink.objects.filter(
        group_id=group_id,
        platform="telegram",
        is_active=True,
        telegram_chat_id__isnull=False,
    ).first()
    return link.telegram_chat_id if link else None


# Welcome message — fires when a group is first linked

@shared_task
def send_bot_welcome_message(group_id: int, platform: str) -> bool:
    """
    Send a welcome message to the connected chat when a group is first linked.
    Called from bot_views.LinkBotGroupView on successful link.
    """
    if platform != "telegram":
        return False

    chat_id = _get_telegram_chat_id(group_id)
    if not chat_id:
        logger.warning({
            "event": "bot_welcome_skipped_no_chat_id",
            "group_id": group_id,
            "platform": platform,
        })
        return False

    try:
        from .models import SavingsGroup
        group = SavingsGroup.objects.get(pk=group_id)
        text = fmt_welcome(
            group_name=group.group_name,
            platform=platform,
            dashboard_url=_group_url(group.public_id or str(group.id)),
        )
        send_message(chat_id, text)
        logger.info({
            "event": "bot_welcome_sent",
            "group_id": group_id,
            "platform": platform,
        })
        return True
    except TelegramError as exc:
        logger.error({
            "event": "bot_welcome_failed",
            "group_id": group_id,
            "error": str(exc),
        })
        return False
    except Exception as exc:
        logger.error({
            "event": "bot_welcome_unexpected_error",
            "group_id": group_id,
            "error": str(exc),
        })
        return False


# Contribution reminder. called by process_daily_payouts orchestrator
# or a dedicated cron that checks upcoming due dates

@shared_task
def send_telegram_contribution_reminder(group_id: int) -> bool:
    """
    Send a contribution reminder to the group's connected Telegram chat.
    """
    chat_id = _get_telegram_chat_id(group_id)
    if not chat_id:
        return False

    try:
        from django.db.models import Sum, Q
        from .models import Contribution, SavingsGroup

        group = SavingsGroup.objects.get(pk=group_id, status="active")
        current_cycle = group.current_cycle_number
        days_left = group.days_until_next_payout or 0

        # Only remind if within 3 days of the due date
        if days_left > 3:
            return False

        text = fmt_contribution_reminder(
            group_name=group.group_name,
            amount=f"{group.contribution_amount:,.2f}",
            cycle=current_cycle,
            days_left=days_left,
            dashboard_url=_group_url(group.public_id or str(group.id)),
        )
        send_message(chat_id, text)
        logger.info({
            "event": "telegram_contribution_reminder_sent",
            "group_id": group_id,
            "cycle": current_cycle,
            "days_left": days_left,
        })
        return True
    except TelegramError as exc:
        logger.error({
            "event": "telegram_contribution_reminder_failed",
            "group_id": group_id,
            "error": str(exc),
        })
        return False
    except Exception as exc:
        logger.error({
            "event": "telegram_contribution_reminder_unexpected",
            "group_id": group_id,
            "error": str(exc),
        })
        return False


# Payout announcement. fires after a successful payout

@shared_task
def send_telegram_payout_announcement(
    group_id: int,
    cycle: int,
    beneficiary_name: str,
    amount: str,
) -> bool:
    """
    Announce a payout in the group's connected Telegram chat.
    """
    chat_id = _get_telegram_chat_id(group_id)
    if not chat_id:
        return False

    try:
        from .models import SavingsGroup
        group = SavingsGroup.objects.get(pk=group_id)
        text = fmt_payout_announcement(
            group_name=group.group_name,
            beneficiary_name=beneficiary_name,
            amount=amount,
            cycle=cycle,
        )
        send_message(chat_id, text)
        logger.info({
            "event": "telegram_payout_announced",
            "group_id": group_id,
            "cycle": cycle,
            "beneficiary": beneficiary_name,
        })
        return True
    except TelegramError as exc:
        logger.error({
            "event": "telegram_payout_announcement_failed",
            "group_id": group_id,
            "error": str(exc),
        })
        return False
    except Exception as exc:
        logger.error({
            "event": "telegram_payout_announcement_unexpected",
            "group_id": group_id,
            "error": str(exc),
        })
        return False


# Group activated. fires when group reaches full capacity

@shared_task
def send_telegram_group_activated(group_id: int) -> bool:
    """
    Announce that the group is now live and payout cycles have started.
    """
    chat_id = _get_telegram_chat_id(group_id)
    if not chat_id:
        return False

    try:
        from .models import SavingsGroup
        group = SavingsGroup.objects.get(pk=group_id)
        text = fmt_group_activated(
            group_name=group.group_name,
            member_count=group.current_members,
            dashboard_url=_group_url(group.public_id or str(group.id)),
        )
        send_message(chat_id, text)
        logger.info({
            "event": "telegram_group_activated_sent",
            "group_id": group_id,
        })
        return True
    except TelegramError as exc:
        logger.error({
            "event": "telegram_group_activated_failed",
            "group_id": group_id,
            "error": str(exc),
        })
        return False
    except Exception as exc:
        logger.error({
            "event": "telegram_group_activated_unexpected",
            "group_id": group_id,
            "error": str(exc),
        })
        return False


# New member joined

@shared_task
def send_telegram_new_member(group_id: int, member_name: str) -> bool:
    """
    Announce a new member joining the group's connected Telegram chat.
    """
    chat_id = _get_telegram_chat_id(group_id)
    if not chat_id:
        return False

    try:
        from .models import SavingsGroup
        group = SavingsGroup.objects.get(pk=group_id)
        text = fmt_new_member(
            group_name=group.group_name,
            member_name=member_name,
            current=group.current_members,
            expected=group.expected_members,
        )
        send_message(chat_id, text)
        logger.info({
            "event": "telegram_new_member_sent",
            "group_id": group_id,
            "member": member_name,
        })
        return True
    except TelegramError as exc:
        logger.error({
            "event": "telegram_new_member_failed",
            "group_id": group_id,
            "error": str(exc),
        })
        return False
    except Exception as exc:
        logger.error({
            "event": "telegram_new_member_unexpected",
            "group_id": group_id,
            "error": str(exc),
        })
        return False


# Contribution progress update. fires after each individual contribution

@shared_task
def send_telegram_contribution_update(group_id: int) -> bool:
    """
    Send a live contribution progress update to the group's Telegram chat.

    Shows a visual progress bar of how many members have contributed this cycle.
    """
    chat_id = _get_telegram_chat_id(group_id)
    if not chat_id:
        return False

    try:
        from django.db.models import Count, Q
        from .models import Contribution, GroupMembership, SavingsGroup

        group = SavingsGroup.objects.get(pk=group_id, status="active")
        current_cycle = group.current_cycle_number

        contributed_count = Contribution.objects.filter(
            membership__group=group,
            cycle_number=current_cycle,
            is_verified=True,
        ).count()

        total_collected = (
            Contribution.objects.filter(
                membership__group=group,
                cycle_number=current_cycle,
                is_verified=True,
            ).aggregate(
                total=__import__("django.db.models", fromlist=["Sum"]).Sum("amount")
            )["total"]
            or Decimal("0.00")
        )

        pot_target = group.total_pot_per_cycle

        text = fmt_contribution_update(
            group_name=group.group_name,
            cycle=current_cycle,
            contributed_count=contributed_count,
            total_members=group.expected_members,
            total_collected=f"{total_collected:,.2f}",
            pot_target=f"{pot_target:,.2f}",
        )
        send_message(chat_id, text)
        logger.info({
            "event": "telegram_contribution_update_sent",
            "group_id": group_id,
            "cycle": current_cycle,
            "contributed": contributed_count,
            "of": group.expected_members,
        })
        return True
    except TelegramError as exc:
        logger.error({
            "event": "telegram_contribution_update_failed",
            "group_id": group_id,
            "error": str(exc),
        })
        return False
    except Exception as exc:
        logger.error({
            "event": "telegram_contribution_update_unexpected",
            "group_id": group_id,
            "error": str(exc),
        })
        return False


# Broadcast contribution reminders to ALL linked groups
# Called by the daily cron task

@shared_task
def broadcast_contribution_reminders() -> str:
    """
    Fan-out task: find all active groups with Telegram links and
    dispatch a send_telegram_contribution_reminder task for each.

    """
    from .models import BotGroupLink

    links = BotGroupLink.objects.filter(
        platform="telegram",
        is_active=True,
        telegram_chat_id__isnull=False,
        group__status="active",
    ).values_list("group_id", flat=True)

    dispatched = 0
    for group_id in links:
        try:
            send_telegram_contribution_reminder.delay(group_id)
            dispatched += 1
        except Exception as exc:
            logger.error({
                "event": "broadcast_reminder_dispatch_failed",
                "group_id": group_id,
                "error": str(exc),
            })

    logger.info({
        "event": "broadcast_contribution_reminders_dispatched",
        "count": dispatched,
    })
    return f"Dispatched {dispatched} contribution reminder tasks."
