# accounts/management/commands/rehash_momo_numbers.py

import hashlib
from django.core.management.base import BaseCommand
from django.conf import settings
from accounts.models import Profile


class Command(BaseCommand):
    help = "Rehash all momo_number_hash fields using the current HASH_SALT"

    def handle(self, *args, **options):
        profiles = Profile.objects.exclude(momo_number__isnull=True).exclude(momo_number='')
        updated = 0
        failed = 0

        for profile in profiles:
            try:
                # Decrypt the encrypted field and rehash with current salt
                raw_momo = str(profile.momo_number)
                normalized = Profile.normalize_momo_number(raw_momo)
                new_hash = Profile.compute_momo_hash(normalized)

                if profile.momo_number_hash != new_hash:
                    profile.momo_number_hash = new_hash
                    profile.save(update_fields=['momo_number_hash'])
                    updated += 1
                    self.stdout.write(f"  Rehashed: {profile.user.email}")

            except Exception as e:
                failed += 1
                self.stdout.write(
                    self.style.ERROR(f"  FAILED for profile {profile.id}: {e}")
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. {updated} rehashed, {failed} failed, "
                f"{profiles.count() - updated - failed} already correct."
            )
        )
