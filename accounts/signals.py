# accounts/signals.py

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from datetime import timedelta
from django.utils import timezone
from .models import Wallet, SavingsGroup


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_wallet(sender, instance, created, **kwargs):
    if created:
        Wallet.objects.get_or_create(user=instance)


@receiver(post_save, sender=SavingsGroup)
def create_group_wallet(sender, instance, created, **kwargs):
    if created:
        Wallet.objects.get_or_create(group=instance)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def send_welcome_notification(sender, instance, created, **kwargs):
    """
    Fires a one-time welcome notification when a new user is created.
    Runs inside on_commit to avoid firing before the transaction commits.
    """
    if not created:
        return

    from django.db import transaction

    def _send():
        try:
            from .services import NotificationService
            NotificationService._create(
                user=instance,
                notification_type='system',
                title='Welcome to SnappX! 👋',
                message=(
                    'Your account is set up. Start by joining a group '
                    'or creating a savings goal.'
                ),
                action_url='Dashboard',
            )
        except Exception:
            pass

    transaction.on_commit(_send)

# # accounts/signals.py  — REPLACE the whole file with this

# from django.db.models.signals import post_save
# from django.dispatch import receiver
# from django.conf import settings
# from .models import Wallet, SavingsGroup


# @receiver(post_save, sender=settings.AUTH_USER_MODEL)
# def create_user_wallet(sender, instance, created, **kwargs):
#     if created:
#         Wallet.objects.get_or_create(user=instance)


# @receiver(post_save, sender=SavingsGroup)
# def create_group_wallet(sender, instance, created, **kwargs):
#     if created:
#         Wallet.objects.get_or_create(group=instance)


# # ── Welcome notification on first login ──────────────────────────────────────
# @receiver(post_save, sender=settings.AUTH_USER_MODEL)
# def send_welcome_notification(sender, instance, created, **kwargs):
#     """
#     Fires a one-time welcome notification when a new user is created.
#     Runs inside on_commit to avoid firing before the transaction commits.
#     """
#     if not created:
#         return

#     from django.db import transaction

#     def _send():
#         try:
#             from .services import NotificationService
#             NotificationService._create(
#                 user=instance,
#                 notification_type='system',
#                 title='Welcome to SnappX! 👋',
#                 message='Your account is set up. Start by joining a group or creating a savings goal.',
#                 action_url='Dashboard',
#             )
#         except Exception:
#             pass  # never break signup

#     transaction.on_commit(_send)

# # accounts/signals.py

# from django.db.models.signals import post_save
# from django.dispatch import receiver
# from django.conf import settings
# from .models import Wallet, SavingsGroup

# @receiver(post_save, sender=settings.AUTH_USER_MODEL)
# def create_user_wallet(sender, instance, created, **kwargs):
#     if created:
#         Wallet.objects.get_or_create(user=instance)

# @receiver(post_save, sender=SavingsGroup)
# def create_group_wallet(sender, instance, created, **kwargs):
#     if created:
#         Wallet.objects.get_or_create(group=instance)
