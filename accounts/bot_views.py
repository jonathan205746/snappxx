# accounts/bot_views.py
"""
Bot integration API endpoints.

Endpoints:
  POST   /accounts/groups/{group_id}/bot/generate-code/
         Admin generates a short-lived link code for their group.

  POST   /accounts/groups/bot/link/
         Verify a link code and create a BotGroupLink record.

  DELETE /accounts/groups/{group_id}/bot/unlink/
         Admin removes a bot link for their group.

  GET    /accounts/groups/bot/linked/
         Return all groups the current user has bot links for.

  GET    /accounts/groups/bot/my-groups/
         Return groups administered by the current user + their link status.

  POST   /accounts/bot/telegram/webhook/
         Receives all updates from Telegram (messages, new chat members, etc.).
         MUST be publicly accessible — verified via secret token header.

  POST   /accounts/bot/telegram/set-webhook/
         One-time setup: register our webhook URL with Telegram.
         Staff-only endpoint.

  GET    /accounts/bot/telegram/health/
         Returns bot info + webhook status. Staff-only health check.
"""

import hashlib
import json
import logging
import secrets
import string

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import BotGroupLink, GroupMembership, SavingsGroup

logger = logging.getLogger("accounts.security")

# ── Constants ──────────────────────────────────────────────────────────────────

_CODE_ALPHABET = string.ascii_uppercase + string.digits
_CODE_TTL_SECONDS = 10 * 60  # 10 minutes
_CODE_CACHE_PREFIX = "bot_link_code:"

# Cache key prefix for pending link codes awaiting chat_id assignment.
# When an admin generates a code and hasn't linked it yet, we store the
# code -> group_id mapping here. When Telegram sends us a /start command
# with the code as the payload, we finalise the link.
_PENDING_LINK_PREFIX = "bot_pending_link:"

PLATFORM_CHOICES = ("whatsapp", "telegram")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _generate_raw_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))


def _format_code(raw: str) -> str:
    return f"SNPX-{raw[:4]}-{raw[4:]}"


def _strip_code(formatted: str) -> str:
    return formatted.replace("-", "").replace(" ", "").upper()


def _cache_key(raw_code: str) -> str:
    return f"{_CODE_CACHE_PREFIX}{raw_code}"


def _resolve_group(group_id_str: str, user):
    qs = SavingsGroup.objects.filter(admin=user)
    group = qs.filter(public_id=group_id_str).first()
    if group:
        return group, None
    if str(group_id_str).isdigit():
        group = qs.filter(pk=int(group_id_str)).first()
        if group:
            return group, None
    return None, Response(
        {"error": "Group not found or you are not the admin."},
        status=status.HTTP_404_NOT_FOUND,
    )


def _verify_telegram_secret(request) -> bool:
    """
    Verify the X-Telegram-Bot-Api-Secret-Token header.
    We set this secret when registering our webhook via set_webhook().
    """
    expected = getattr(settings, "TELEGRAM_WEBHOOK_SECRET", None)
    if not expected:
        # If no secret is configured, skip verification (dev mode)
        return True
    received = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    return secrets.compare_digest(received, expected)


# ── View: Generate Link Code ───────────────────────────────────────────────────


@extend_schema(
    request=inline_serializer(
        name="GenerateBotCodeRequest",
        fields={"platform": serializers.ChoiceField(choices=PLATFORM_CHOICES)},
    ),
    responses={
        200: inline_serializer(
            name="GenerateBotCodeResponse",
            fields={
                "link_code": serializers.CharField(),
                "expires_in_seconds": serializers.IntegerField(),
                "platform": serializers.CharField(),
                "group_name": serializers.CharField(),
                "warning": serializers.CharField(required=False),
            },
        ),
    },
    description=(
        "Generate a short-lived link code for connecting a messaging bot to this group. "
        "Only the group admin can call this. Expires in 10 minutes."
    ),
    tags=["Bot Integration"],
)
class GenerateBotLinkCodeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, group_id):
        platform = request.data.get("platform", "").lower()
        if platform not in PLATFORM_CHOICES:
            return Response(
                {"error": f"platform must be one of: {', '.join(PLATFORM_CHOICES)}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        group, err = _resolve_group(str(group_id), request.user)
        if err:
            return err

        already_linked = BotGroupLink.objects.filter(
            group=group, platform=platform, is_active=True
        ).exists()

        raw_code = _generate_raw_code()
        formatted_code = _format_code(raw_code)

        cache.set(
            _cache_key(raw_code),
            {"group_id": group.id, "platform": platform},
            timeout=_CODE_TTL_SECONDS,
        )

        logger.info({
            "event": "bot_link_code_generated",
            "admin_id": request.user.id,
            "group_id": group.id,
            "platform": platform,
        })

        response_data = {
            "link_code": formatted_code,
            "expires_in_seconds": _CODE_TTL_SECONDS,
            "platform": platform,
            "group_name": group.group_name,
        }
        if already_linked:
            response_data["warning"] = (
                f"This group is already linked to {platform}. "
                "Submitting this code will update the existing link."
            )

        return Response(response_data, status=status.HTTP_200_OK)


# ── View: Link Group ───────────────────────────────────────────────────────────


@extend_schema(
    request=inline_serializer(
        name="LinkBotGroupRequest",
        fields={
            "link_code": serializers.CharField(),
            "platform": serializers.ChoiceField(choices=PLATFORM_CHOICES),
        },
    ),
    responses={
        201: inline_serializer(
            name="LinkBotGroupResponse",
            fields={
                "success": serializers.BooleanField(),
                "message": serializers.CharField(),
                "group_id": serializers.IntegerField(),
                "group_name": serializers.CharField(),
                "public_id": serializers.CharField(),
                "platform": serializers.CharField(),
                "linked_at": serializers.DateTimeField(),
                "next_step": serializers.CharField(),
            },
        ),
    },
    description=(
        "Verify a bot link code and save the connection. "
        "For Telegram, the chat_id is assigned when the bot receives /start in the group."
    ),
    tags=["Bot Integration"],
)
class LinkBotGroupView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        raw_input = request.data.get("link_code", "")
        platform = request.data.get("platform", "").lower()

        if not raw_input:
            return Response(
                {"error": "link_code is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if platform not in PLATFORM_CHOICES:
            return Response(
                {"error": f"platform must be one of: {', '.join(PLATFORM_CHOICES)}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        normalised = _strip_code(raw_input)
        if normalised.startswith("SNPX"):
            normalised = normalised[4:]

        if len(normalised) != 8:
            return Response(
                {"error": "Invalid link code format. Expected SNPX-XXXX-XXXX."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        cached = cache.get(_cache_key(normalised))
        if not cached:
            return Response(
                {"error": "Link code is invalid or has expired. Please generate a new one."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if cached["platform"] != platform:
            return Response(
                {
                    "error": (
                        f"This code was generated for {cached['platform']}, "
                        f"not {platform}. Please select the correct platform."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        group_id = cached["group_id"]
        try:
            group = SavingsGroup.objects.get(pk=group_id)
        except SavingsGroup.DoesNotExist:
            return Response(
                {"error": "Group no longer exists."},
                status=status.HTTP_404_NOT_FOUND,
            )

        is_member = GroupMembership.objects.filter(
            user=request.user, group=group
        ).exists()
        is_admin = group.admin_id == request.user.id

        if not is_member and not is_admin:
            return Response(
                {"error": "You must be a member of this group to link it."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Consume the code (one-time use)
        cache.delete(_cache_key(normalised))

        # For Telegram: create the link record now (chat_id arrives later via webhook)
        # For WhatsApp: same pattern
        link, created = BotGroupLink.objects.update_or_create(
            group=group,
            platform=platform,
            defaults={
                "linked_by": request.user,
                "is_active": True,
                "last_activity": timezone.now(),
                # chat_id is null until the bot receives /start in the group
                "telegram_chat_id": None,
            },
        )

        # Store a pending entry in cache so the Telegram webhook can find
        # which group to attach the chat_id to when /start arrives.
        # Key: "bot_pending_link:{group_id}:telegram"
        # This lets the webhook complete the final step automatically.
        if platform == "telegram":
            pending_key = f"{_PENDING_LINK_PREFIX}{group.id}:telegram"
            cache.set(pending_key, {"group_id": group.id}, timeout=60 * 60 * 24)  # 24h

        logger.info({
            "event": "bot_group_linked",
            "user_id": request.user.id,
            "group_id": group.id,
            "platform": platform,
            "created": created,
        })

        return Response(
            {
                "success": True,
                "message": (
                    f'"{group.group_name}" is now linked to {platform.capitalize()}. '
                    f"Add the bot to your {platform.capitalize()} group and send "
                    f"{'!join' if platform == 'whatsapp' else '/start'} to activate."
                ),
                "group_id": group.id,
                "group_name": group.group_name,
                "public_id": group.public_id,
                "platform": platform,
                "linked_at": link.linked_at.isoformat(),
                "next_step": "add_bot_to_group",
            },
            status=status.HTTP_201_CREATED,
        )


# ── View: Unlink ───────────────────────────────────────────────────────────────


@extend_schema(
    request=inline_serializer(
        name="UnlinkBotGroupRequest",
        fields={"platform": serializers.ChoiceField(choices=PLATFORM_CHOICES)},
    ),
    responses={
        200: inline_serializer(
            name="UnlinkBotGroupResponse",
            fields={"success": serializers.BooleanField(), "message": serializers.CharField()},
        ),
    },
    description="Remove a bot link. Only the group admin can unlink.",
    tags=["Bot Integration"],
)
class UnlinkBotGroupView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def delete(self, request, group_id):
        platform = request.data.get("platform", "").lower()
        if platform not in PLATFORM_CHOICES:
            return Response(
                {"error": f"platform must be one of: {', '.join(PLATFORM_CHOICES)}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        group, err = _resolve_group(str(group_id), request.user)
        if err:
            return err

        deleted_count, _ = BotGroupLink.objects.filter(
            group=group, platform=platform
        ).delete()

        if deleted_count == 0:
            return Response(
                {"error": f"No {platform} link found for this group."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Clean up pending cache entry
        if platform == "telegram":
            cache.delete(f"{_PENDING_LINK_PREFIX}{group.id}:telegram")

        logger.info({
            "event": "bot_group_unlinked",
            "admin_id": request.user.id,
            "group_id": group.id,
            "platform": platform,
        })

        return Response(
            {
                "success": True,
                "message": f'"{group.group_name}" has been disconnected from {platform.capitalize()}.',
            },
            status=status.HTTP_200_OK,
        )


# ── View: List Linked Groups ───────────────────────────────────────────────────


@extend_schema(
    tags=["Bot Integration"],
    description="Return all BotGroupLink records for groups the current user belongs to.",
)
class LinkedGroupsListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        member_group_ids = GroupMembership.objects.filter(
            user=user
        ).values_list("group_id", flat=True)

        admin_group_ids = SavingsGroup.objects.filter(
            admin=user
        ).values_list("id", flat=True)

        all_group_ids = set(list(member_group_ids) + list(admin_group_ids))

        links = (
            BotGroupLink.objects.filter(group_id__in=all_group_ids)
            .select_related("group__admin")
            .order_by("-linked_at")
        )

        results = []
        for link in links:
            group = link.group
            results.append({
                "id": f"{group.id}-{link.platform}",
                "group_id": group.id,
                "group_name": group.group_name,
                "public_id": group.public_id or str(group.id),
                "platform": link.platform,
                "member_count": group.current_members,
                # A link is fully active only when chat_id has been assigned
                "is_active": link.is_active and bool(
                    link.telegram_chat_id if link.platform == "telegram" else True
                ),
                # Pending = linked in DB but bot not yet added to the chat
                "is_pending": link.platform == "telegram" and not link.telegram_chat_id,
                "is_admin": group.admin_id == user.id,
                "linked_at": link.linked_at.isoformat(),
                "last_activity": link.last_activity.isoformat() if link.last_activity else None,
                "group_status": group.status,
            })

        return Response({"results": results, "count": len(results)})


# ── View: Admin Groups for Bot ─────────────────────────────────────────────────


@extend_schema(
    tags=["Bot Integration"],
    description="Return groups administered by the current user with their bot link status.",
)
class AdminGroupsForBotView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        groups = SavingsGroup.objects.filter(
            admin=request.user
        ).prefetch_related("bot_links").order_by("-created_at")

        results = []
        for group in groups:
            linked_platforms = [
                link.platform
                for link in group.bot_links.all()
                if link.is_active
            ]
            results.append({
                "id": group.id,
                "group_name": group.group_name,
                "public_id": group.public_id or str(group.id),
                "status": group.status,
                "current_members": group.current_members,
                "expected_members": group.expected_members,
                "linked_platforms": linked_platforms,
            })

        return Response({"results": results})


# ── View: Telegram Webhook ─────────────────────────────────────────────────────


@extend_schema(exclude=True)
class TelegramWebhookView(APIView):
    """
    Receives all updates from Telegram.

    Security: verified via X-Telegram-Bot-Api-Secret-Token header.
    Always returns 200 so Telegram doesn't retry failed deliveries.

    Handled update types:
    - /start command with a group link code payload
      → assigns the chat_id to the pending BotGroupLink record
      → sends a welcome message
    - New chat members (bot was added to a group)
      → logs the event (chat_id assignment still needs /start)
    - Left chat member (bot was removed)
      → marks the BotGroupLink as inactive
    """
    permission_classes = []
    authentication_classes = []

    @csrf_exempt
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def post(self, request):
        # Verify secret token
        if not _verify_telegram_secret(request):
            logger.warning({
                "event": "telegram_webhook_invalid_secret",
                "ip": request.META.get("REMOTE_ADDR"),
            })
            # Still return 200 — returning 4xx causes Telegram to retry
            return Response({"status": "ignored"}, status=200)

        try:
            update = json.loads(request.body)
        except json.JSONDecodeError:
            return Response({"status": "bad_json"}, status=200)

        try:
            self._route_update(update)
        except Exception as exc:
            logger.error({
                "event": "telegram_webhook_handler_error",
                "error": str(exc),
                "update_id": update.get("update_id"),
            })

        return Response({"status": "ok"}, status=200)

    def _route_update(self, update: dict):
        message = update.get("message") or update.get("my_chat_member")

        if not message:
            return

        # ── /start command (possibly with link payload) ────────────────────
        text = message.get("text", "")
        if text.startswith("/start"):
            self._handle_start(message)
            return

        # ── Bot was removed from a group ───────────────────────────────────
        new_chat_member = update.get("my_chat_member", {}).get("new_chat_member", {})
        if new_chat_member.get("status") in ("kicked", "left"):
            self._handle_bot_removed(update["my_chat_member"])
            return

    # def _handle_start(self, message: dict):
    #     """
    #     /start is sent when the bot is activated in a group or private chat.

    #     When an admin adds the bot to a Telegram group and sends /start, we:
    #     1. Extract the chat_id from the message
    #     2. Look up the pending BotGroupLink for this group
    #     3. Assign the chat_id, making the link fully active
    #     4. Send a welcome message
    #     """
    #     chat = message.get("chat", {})
    #     chat_id = str(chat.get("id", ""))
    #     chat_type = chat.get("type", "")  # "group", "supergroup", "private"

    #     if not chat_id:
    #         return

    #     # Only handle group/supergroup chats — not private messages to the bot
    #     if chat_type not in ("group", "supergroup"):
    #         logger.info({
    #             "event": "telegram_start_in_private_chat",
    #             "chat_id": chat_id,
    #         })
    #         return

    #     # Find a BotGroupLink that is pending chat_id assignment for this chat.
    #     # We match by looking for any telegram link without a chat_id.
    #     # In practice, admins link one group at a time, so there's rarely ambiguity.
    #     # A more robust approach (future): embed the group_id in the /start payload.
    #     pending_links = BotGroupLink.objects.filter(
    #         platform="telegram",
    #         is_active=True,
    #         telegram_chat_id__isnull=True,
    #     ).select_related("group")

    #     if not pending_links.exists():
    #         logger.info({
    #             "event": "telegram_start_no_pending_link",
    #             "chat_id": chat_id,
    #         })
    #         return

    #     # Assign chat_id to the most recently created pending link
    #     link = pending_links.order_by("-linked_at").first()
    #     link.telegram_chat_id = chat_id
    #     link.last_activity = timezone.now()
    #     link.save(update_fields=["telegram_chat_id", "last_activity"])

    #     logger.info({
    #         "event": "telegram_chat_id_assigned",
    #         "group_id": link.group_id,
    #         "chat_id": chat_id,
    #     })

    #     # Send welcome message directly in the webhook handler
    #     from .telegram import TelegramError, fmt_welcome, send_message
    #     try:
    #         frontend_url = getattr(settings, "FRONTEND_URL", "https://www.snappx.app")
    #         dashboard_url = f"{frontend_url}/groups/{link.group.public_id or str(link.group_id)}"
    #         text = fmt_welcome(
    #             group_name=link.group.group_name,
    #             platform="telegram",
    #             dashboard_url=dashboard_url,
    #         )
    #         send_message(chat_id, text)
    #         logger.info({
    #             "event": "telegram_welcome_sent",
    #             "group_id": link.group_id,
    #             "chat_id": chat_id,
    #         })
    #     except TelegramError as exc:
    #         logger.error({
    #             "event": "telegram_welcome_failed",
    #             "group_id": link.group_id,
    #             "error": str(exc),
    #         })


    # def _handle_start(self, message: dict):
    #     chat = message.get("chat", {})
    #     chat_id = str(chat.get("id", ""))
    #     chat_type = chat.get("type", "")

    #     logger.warning({
    #         "event": "telegram_start_received",
    #         "chat_id": chat_id,
    #         "chat_type": chat_type,
    #         "message": str(message)[:200],
    #     })

    #     if not chat_id:
    #         logger.warning({"event": "telegram_start_no_chat_id"})
    #         return

    #     if chat_type not in ("group", "supergroup"):
    #         logger.warning({
    #             "event": "telegram_start_wrong_chat_type",
    #             "chat_type": chat_type,
    #         })
    #         return

    #     pending_links = BotGroupLink.objects.filter(
    #         platform="telegram",
    #         is_active=True,
    #         telegram_chat_id__isnull=True,
    #     ).select_related("group")

    #     logger.warning({
    #         "event": "telegram_pending_links_count",
    #         "count": pending_links.count(),
    #     })

    #     if not pending_links.exists():
    #         logger.warning({
    #             "event": "telegram_start_no_pending_link",
    #             "chat_id": chat_id,
    #         })
    #         return

    #     link = pending_links.order_by("-linked_at").first()
    #     link.telegram_chat_id = chat_id
    #     link.last_activity = timezone.now()
    #     link.save(update_fields=["telegram_chat_id", "last_activity"])

    #     logger.warning({
    #         "event": "telegram_chat_id_assigned",
    #         "group_id": link.group_id,
    #         "chat_id": chat_id,
    #     })

    #     from .telegram import TelegramError, fmt_welcome, send_message
    #     try:
    #         frontend_url = getattr(settings, "FRONTEND_URL", "https://www.snappx.app")
    #         dashboard_url = f"{frontend_url}/groups/{link.group.public_id or str(link.group_id)}"
    #         text = fmt_welcome(
    #             group_name=link.group.group_name,
    #             platform="telegram",
    #             dashboard_url=dashboard_url,
    #         )
    #         logger.warning({
    #             "event": "telegram_welcome_attempting",
    #             "chat_id": chat_id,
    #             "text_length": len(text),
    #         })
    #         send_message(chat_id, text)
    #         logger.warning({
    #             "event": "telegram_welcome_sent_success",
    #             "chat_id": chat_id,
    #         })
    #     except TelegramError as exc:
    #         logger.warning({
    #             "event": "telegram_welcome_failed",
    #             "group_id": link.group_id,
    #             "error": str(exc),
    #             "error_code": exc.error_code,
    #             "raw": str(exc.raw),
    #         })
    #     except Exception as exc:
    #         logger.warning({
    #             "event": "telegram_welcome_unexpected",
    #             "error": str(exc),
    #             "type": type(exc).__name__,
    #         })


    def _handle_start(self, message: dict):
        chat = message.get("chat", {})
        chat_id = str(chat.get("id", ""))
        chat_type = chat.get("type", "")

        if not chat_id:
            return

        if chat_type not in ("group", "supergroup"):
            return

        pending_links = BotGroupLink.objects.filter(
            platform="telegram",
            is_active=True,
            telegram_chat_id__isnull=True,
        ).select_related("group")

        if not pending_links.exists():
            logger.info({
                "event": "telegram_start_no_pending_link",
                "chat_id": chat_id,
            })
            return

        link = pending_links.order_by("-linked_at").first()
        link.telegram_chat_id = chat_id
        link.last_activity = timezone.now()
        link.save(update_fields=["telegram_chat_id", "last_activity"])

        logger.info({
            "event": "telegram_chat_id_assigned",
            "group_id": link.group_id,
            "chat_id": chat_id,
        })

        from .telegram import TelegramError, fmt_welcome, send_message
        try:
            frontend_url = getattr(settings, "FRONTEND_URL", "https://www.snappx.app")
            dashboard_url = f"{frontend_url}/groups/{link.group.public_id or str(link.group_id)}"
            text = fmt_welcome(
                group_name=link.group.group_name,
                platform="telegram",
                dashboard_url=dashboard_url,
            )
            send_message(chat_id, text)
            logger.info({
                "event": "telegram_welcome_sent",
                "group_id": link.group_id,
                "chat_id": chat_id,
            })
        except TelegramError as exc:
            logger.error({
                "event": "telegram_welcome_failed",
                "group_id": link.group_id,
                "error": str(exc),
            })
        except Exception as exc:
            logger.error({
                "event": "telegram_welcome_unexpected",
                "group_id": link.group_id,
                "error": str(exc),
            })

    def _handle_bot_removed(self, my_chat_member: dict):
        """Bot was kicked or left a group — mark the link as inactive."""
        chat_id = str(my_chat_member.get("chat", {}).get("id", ""))
        if not chat_id:
            return

        updated = BotGroupLink.objects.filter(
            platform="telegram",
            telegram_chat_id=chat_id,
        ).update(is_active=False)

        if updated:
            logger.info({
                "event": "telegram_bot_removed_from_group",
                "chat_id": chat_id,
            })


# ── View: Set Webhook (one-time setup) ────────────────────────────────────────


@extend_schema(
    request=inline_serializer(
        name="SetWebhookRequest",
        fields={
            "webhook_url": serializers.URLField(
                help_text="Full public URL, e.g. https://your-domain.com/api/accounts/bot/telegram/webhook/"
            )
        },
    ),
    responses={
        200: inline_serializer(
            name="SetWebhookResponse",
            fields={
                "success": serializers.BooleanField(),
                "telegram_response": serializers.DictField(),
            },
        ),
    },
    description=(
        "One-time setup: register the webhook URL with Telegram. "
        "Staff only. Call this once after deploying to production."
    ),
    tags=["Bot Integration"],
)
class SetTelegramWebhookView(APIView):
    permission_classes = [IsAuthenticated, IsAdminUser]

    def post(self, request):
        webhook_url = request.data.get("webhook_url", "").strip()
        if not webhook_url:
            return Response(
                {"error": "webhook_url is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from .telegram import TelegramError, set_webhook
        secret = getattr(settings, "TELEGRAM_WEBHOOK_SECRET", None)

        try:
            result = set_webhook(webhook_url, secret_token=secret)
            logger.info({
                "event": "telegram_webhook_registered",
                "url": webhook_url,
                "admin_id": request.user.id,
            })
            return Response({"success": True, "telegram_response": result})
        except TelegramError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )


# ── View: Health check ─────────────────────────────────────────────────────────


@extend_schema(
    responses={200: {"description": "Bot info + webhook status"}},
    description="Returns bot info and current webhook configuration. Staff only.",
    tags=["Bot Integration"],
)
class TelegramBotHealthView(APIView):
    permission_classes = [IsAuthenticated, IsAdminUser]

    def get(self, request):
        from .telegram import TelegramError, get_me, get_webhook_info
        try:
            bot_info = get_me()
            webhook_info = get_webhook_info()
            return Response({
                "bot": bot_info,
                "webhook": webhook_info,
                "token_configured": bool(getattr(settings, "TELEGRAM_BOT_TOKEN", None)),
            })
        except TelegramError as exc:
            return Response(
                {"error": str(exc), "token_configured": bool(getattr(settings, "TELEGRAM_BOT_TOKEN", None))},
                status=status.HTTP_502_BAD_GATEWAY,
            )
