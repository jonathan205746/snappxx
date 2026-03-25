# accounts/signals.py

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from .models import Wallet, SavingsGroup

@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_wallet(sender, instance, created, **kwargs):
    if created:
        Wallet.objects.get_or_create(user=instance)

@receiver(post_save, sender=SavingsGroup)
def create_group_wallet(sender, instance, created, **kwargs):
    if created:
        Wallet.objects.get_or_create(group=instance)
