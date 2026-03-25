# accounts/apps.py

from django.apps import AppConfig

class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'

    def ready(self):
        import accounts.signals

        from auditlog.registry import auditlog
        from .models import (
            SavingsGroup, SavingsGoal, Profile,
            LedgerEntry, Wallet, Contribution
        )

        # Register models for audit tracking
        auditlog.register(SavingsGroup)
        auditlog.register(SavingsGoal)
        auditlog.register(Profile)
        auditlog.register(Wallet)
        auditlog.register(Contribution)
        auditlog.register(LedgerEntry)
