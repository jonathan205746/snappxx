
# accounts/tasks.py

import requests
from decimal import Decimal
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.contrib.sites.models import Site
from django.urls import NoReverseMatch, reverse
from django.db import models, transaction
from django.utils import timezone
from datetime import timedelta
from django.db.models import Sum
from django_qstash import stashed_task
# from celery import shared_task
from .models import SavingsGroup, Contribution, PayoutOrder, SavingsGoal, Wallet, LedgerEntry, IdempotencyKey, GroupJoinRequest
from .services import LedgerService
import logging


logger = logging.getLogger('accounts.finance')


# Dawurobo API constants
DAWUROBO_BASE = "https://devs.sms.api.dawurobo.com/v1/otp"

HEADERS = {
    "accept": "application/json",
    "x-api-key": settings.DAWUROBO_API_KEY,
    "x-access-token": settings.DAWUROBO_ACCESS_TOKEN,
    "Content-Type": "application/json"
}


@stashed_task(bind=True, max_retries=3, default_retry_delay=60)
def send_dawurobo_otp_async(self, phone_number: str):
    """
    GOOGLE FIX: OTP sending MUST be async.
    A 30s timeout in a request-response cycle is a DoS risk.
    """
    clean_number = phone_number.replace("+", "").replace(" ", "")
    payload = {
        "senderid": settings.DAWUROBO_SENDER_ID,
        "number": clean_number,
        "messagetemplate": "Your SnappX code: %OTPCODE%. Expires in %EXPIRY% min.",
        "expiry": 10,
        "length": 6,
        "type": "NUMERIC"
    }

    try:
        response = requests.post(f"{DAWUROBO_BASE}/generate", json=payload, headers=HEADERS, timeout=15)
        response.raise_for_status()
        return {"success": True}
    except Exception as exc:
        logger.error(f"OTP SEND FAIL: {phone_number} - {exc}")
        raise self.retry(exc=exc)


def verify_and_invalidate_otp_sync(phone_number: str, code: str) -> bool:
    """
    Secure synchronous verification + immediate invalidation.
    Used for signup verification and password reset.
    """
    clean_number = phone_number.replace("+", "").replace(" ", "")
    verify_payload = {"otpcode": code.upper(), "number": clean_number}

    try:
        verify_resp = requests.post(
            f"{DAWUROBO_BASE}/verify",
            json=verify_payload,
            headers=HEADERS,
            timeout=10
        )

        success = verify_resp.status_code == 200 and "success" in verify_resp.text.lower()

        if success:
            # Immediately invalidate to prevent reuse
            requests.post(
                f"{DAWUROBO_BASE}/invalidate",
                json={"number": clean_number},
                headers=HEADERS,
                timeout=3
            )
            print(f"OTP verified and invalidated for {phone_number}")
            return True
        else:
            print(f"Invalid OTP attempt: {verify_resp.text}")
            return False

    except Exception as e:
        print(f"OTP verify/invalidate failed: {e}")
        return False


@stashed_task
def send_group_join_request_email_async(request_id: int):
    """
    Celery task to send an email to the Group Admin notifying them
    of a new join request.
    """
    try:
        from .models import GroupJoinRequest

        join_request = GroupJoinRequest.objects.select_related(
            'group__admin', 'user__profile'
        ).get(pk=request_id)

    except GroupJoinRequest.DoesNotExist:
        print(f"ERROR: GroupJoinRequest with ID {request_id} not found.")
        return False

    group = join_request.group
    admin_user = group.admin
    requester_name = join_request.user.profile.full_name

    # Construct the Deep Link URL
    current_site = Site.objects.get_current()
    protocol = "http" if settings.DEBUG else "https"

    try:
        relative_url = reverse('group-requests-list', kwargs={'group_id': group.id})
    except NoReverseMatch:
        print("URL Reverse Match Error: Check URL configuration for 'group-requests-list'")
        return False

    full_review_url = f"{protocol}://{current_site.domain}{relative_url}"

    # Render Email Content
    context = {
        'admin_name': admin_user.profile.full_name,
        'group_name': group.group_name,
        'requester_name': requester_name,
        'review_url': full_review_url,
    }

    email_html_content = render_to_string('emails/new_join_request.html', context)
    email_text_content = f"A new user, {requester_name}, has requested to join your group: {group.group_name}. Review the request here: {full_review_url}"

    # Send Email
    try:
        send_mail(
            subject=f"🚀 New Join Request for '{group.group_name}'",
            message=email_text_content,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[admin_user.email],
            html_message=email_html_content,
            fail_silently=False,
        )
        return True
    except Exception as e:
        print(f"EMAIL SEND ERROR for Group ID {group.id}: {e}")
        return False


@stashed_task
def send_group_join_response_email_async(request_id: int, action: str):
    """
    Celery task to send an email to the applicant (user) notifying them
    that their join request has been approved or rejected by the admin.
    """
    try:
        from .models import GroupJoinRequest

        join_request = GroupJoinRequest.objects.select_related(
            'group__admin', 'user__profile'
        ).get(pk=request_id)

    except GroupJoinRequest.DoesNotExist:
        print(f"ERROR: GroupJoinRequest with ID {request_id} not found for response.")
        return False

    group = join_request.group
    applicant_user = join_request.user
    applicant_name = applicant_user.profile.full_name

    clean_group_name = group.group_name.strip('*').strip()

    # Construct the Deep Link URL
    current_site = Site.objects.get_current()
    protocol = "http" if settings.DEBUG else "https"

    try:
        relative_url = reverse('group-detail', kwargs={'id': group.id})
    except NoReverseMatch:
        print("URL Reverse Match Error: Check URL configuration for 'group-detail'")
        relative_url = reverse('group_list_all')

    full_group_url = f"{protocol}://{current_site.domain}{relative_url}"

    # Determine Subject and Template based on action
    if action == 'approved':
        subject = f"🎉 Welcome! You've Joined '{clean_group_name}'"
        template_name = 'emails/join_request_approved.html'
        message_prefix = "Congratulations! Your request to join"
    elif action == 'rejected':
        subject = f"😔 Update: Request to Join '{clean_group_name}'"
        template_name = 'emails/join_request_rejected.html'
        message_prefix = "Unfortunately, your request to join"
    else:
        print(f"ERROR: Invalid action '{action}' passed to email task.")
        return False

    # Render Email Content
    context = {
        'applicant_name': applicant_name,
        'group_name': group.group_name,
        'group_url': full_group_url,
        'action': action,
        'admin_name': group.admin.profile.full_name,
    }

    email_html_content = render_to_string(template_name, context)
    email_text_content = (
    f"Update for group '{clean_group_name}': Your request was {action}. "
    f"Log in to view details: {full_group_url}"
)

    # Send Email
    try:
        send_mail(
            subject=subject,
            message=email_text_content,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[applicant_user.email],
            html_message=email_html_content,
            fail_silently=False,
        )
        print(f"Group join response '{action}' email sent to {applicant_user.email}")
        return True
    except Exception as e:
        print(f"EMAIL SEND ERROR for Request ID {request_id}: {e}")
        return False


@stashed_task(bind=True, max_retries=3)
def process_daily_payouts(self):
    """
    High-performance daily orchestrator for group disbursements.
    Uses database-level filtering and iterators to handle 100k+ groups efficiently.
    """
    today = timezone.now().date()
    queryset = SavingsGroup.objects.filter(
        status='active',
        start_date__lte=today
    ).only('id', 'start_date', 'payout_interval_days').iterator(chunk_size=1000)

    dispatched_count = 0

    for group in queryset:
        days_since_start = (today - group.start_date).days

        if days_since_start > 0 and days_since_start % group.payout_interval_days == 0:
            execute_group_payout.stash(group.id)
            dispatched_count += 1

    return f"Orchestrator finished. Dispatched {dispatched_count} payout tasks."

@stashed_task(bind=True, max_retries=2)
def execute_group_payout(self, group_id):
    """
    Atomic Payout Logic.
    Ensures 'Conservation of Money' and 'At-Most-Once' execution.
    """
    try:
        with transaction.atomic():
            group = SavingsGroup.objects.select_for_update().get(id=group_id)
            current_cycle = group.current_cycle_number

            payout_ref = f"PAYOUT-GRP{group.id}-C{current_cycle}"
            if LedgerEntry.objects.filter(reference__contains=payout_ref).exists():
                logger.warning(f"Aborting: Cycle {current_cycle} for Group {group_id} already disbursed.")
                return

            verified_count = Contribution.objects.filter(
                membership__group=group, cycle_number=current_cycle, is_verified=True
            ).count()

            if verified_count < group.expected_members:
                logger.error(f"Payout Failed: Insufficient funds for Group {group_id} Cycle {current_cycle}")
                return

            position = ((current_cycle - 1) % group.expected_members) + 1
            payout_order = PayoutOrder.objects.select_related('membership__user').get(
                group=group, position=position
            )
            beneficiary = payout_order.membership.user

            total_pot = group.total_pot_per_cycle

            LedgerService.transfer(
                from_group=group,
                to_user=beneficiary,
                amount=total_pot,
                transaction_type='payout',
                description=f"Payout Cycle {current_cycle} to {beneficiary.profile.full_name}",
                reference=payout_ref,
                actor=group.admin,
                request_id=None
            )

            group.current_cycle_number += 1
            if current_cycle >= group.expected_members:
                group.status = 'completed'
            group.save()

            transaction.on_commit(lambda: send_payout_notification_email_async.delay(
                beneficiary.id, group.id, current_cycle, str(total_pot)
            ))

    except Exception as exc:
        logger.critical(f"CRITICAL PAYOUT ERROR: Group {group_id} - {exc}")
        raise self.retry(exc=exc)

@stashed_task
def send_payout_notification_email_async(
    beneficiary_id: int,
    group_id: int,
    cycle: int,
    amount: str
) -> bool:
    """
    Celery task to send a payout notification email to the beneficiary.
    """
    try:
        from .models import SavingsGroup, User
        beneficiary = User.objects.select_related('profile').get(pk=beneficiary_id)
        group = SavingsGroup.objects.get(pk=group_id)
    except (User.DoesNotExist, SavingsGroup.DoesNotExist):
        print(f"ERROR: User ID {beneficiary_id} or Group ID {group_id} not found.")
        return False

    beneficiary_name = beneficiary.profile.full_name
    group_name = group.group_name.strip('*').strip()
    momo_number = str(beneficiary.profile.momo_number)
    formatted_amount = f"₵{Decimal(amount):,.2f}"

    current_site = Site.objects.get_current()
    protocol = "http" if settings.DEBUG else "https"
    try:
        relative_url = reverse('dashboard')
    except NoReverseMatch:
        print("URL Reverse Match Error: Check URL configuration for 'dashboard'")
        relative_url = reverse('group-detail', kwargs={'id': group.id})
    full_dashboard_url = f"{protocol}://{current_site.domain}{relative_url}"

    # Render Email Content
    context = {
        'beneficiary_name': beneficiary_name,
        'group_name': group_name,
        'cycle': cycle,
        'amount': formatted_amount,
        'momo_number': momo_number,
        'dashboard_url': full_dashboard_url,
    }
    template_name = 'emails/payout_notification.html'
    subject = f"🎉 Payout Processed from '{group_name}'!"
    email_html_content = render_to_string(template_name, context)
    email_text_content = (
        f"Congratulations, {beneficiary_name}! Your payout of {formatted_amount} "
        f"for cycle {cycle} in '{group_name}' has been processed. "
        f"It will be sent to your MoMo account: {momo_number}. "
        f"View your dashboard: {full_dashboard_url}"
    )

    try:
        send_mail(
            subject=subject,
            message=email_text_content,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[beneficiary.email],
            html_message=email_html_content,
            fail_silently=False,
        )
        print(f"Payout notification email sent to {beneficiary.email} for Group ID {group.id}")
        return True
    except Exception as e:
        print(f"EMAIL SEND ERROR for Beneficiary ID {beneficiary_id}: {e}")
        return False

@stashed_task
def send_goal_reminders():
    """
    Celery task to check all active savings goals and send reminder emails.
    Includes precision handling for financial progress and safety checks.
    """
    now = timezone.now()
    today = now.date()


    goals = SavingsGoal.objects.annotate(
        total_saved=Sum('contributions__amount')
    ).filter(
        total_saved__lt=models.F('target_amount')
    ).select_related('user__profile')

    for goal in goals:
        current_saved = goal.total_saved or Decimal('0.00')
        target = goal.target_amount

        if not goal.is_due:
            continue

        if goal.last_reminded_at and (now - goal.last_reminded_at) < timedelta(days=1):
            continue

        if target > 0:
            progress_decimal = (current_saved / target) * 100
            progress_percentage = float(round(progress_decimal, 1))
        else:
            progress_percentage = 0.0

        user = goal.user
        user_name = user.profile.full_name or "there"

        current_site = Site.objects.get_current()
        protocol = "http" if settings.DEBUG else "https"

        try:
            dashboard_url = f"{protocol}://{current_site.domain}{reverse('goals-dashboard')}"
        except NoReverseMatch:
            dashboard_url = f"{protocol}://{current_site.domain}/dashboard/"

        context = {
            'user_name': user_name,
            'goal_name': goal.name,
            'contribution_amount': f"₵{goal.regular_contribution:,.2f}",
            'frequency': goal.get_frequency_display(),
            'current_saved': f"₵{current_saved:,.2f}",
            'target_amount': f"₵{target:,.2f}",
            'progress_percentage': progress_percentage,
            'target_date': goal.target_date.strftime('%B %d, %Y'),
            'dashboard_url': dashboard_url,
        }

        subject = f"⏰ Reminder: Your {goal.get_frequency_display()} contribution for '{goal.name}'"
        html_content = render_to_string('emails/goal_reminder.html', context)

        text_content = (
            f"Hi {user_name},\n\n"
            f"Friendly reminder to make your {goal.get_frequency_display()} contribution of "
            f"₵{goal.regular_contribution:,.2f} towards your goal: \"{goal.name}\".\n\n"
            f"Current Progress: {progress_percentage}% (₵{current_saved:,.2f} of ₵{target:,.2f})\n"
            f"Target Date: {goal.target_date.strftime('%B %d, %Y')}\n\n"
            f"Keep up the great work! Log in here to contribute: {dashboard_url}"
        )

        try:
            send_mail(
                subject=subject,
                message=text_content,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                html_message=html_content,
                fail_silently=False,
            )

            goal.last_reminded_at = now
            goal.save(update_fields=['last_reminded_at'])

            logger.info(f"Goal reminder sent to {user.email} for goal: {goal.name}")

        except Exception as e:
            logger.error(f"Failed to send goal reminder to {user.id}: {str(e)}")


def send_reconciliation_alert_to_admins(mismatches):
    """
    Sends a high-priority email to system administrators when
    financial drift is detected between the Ledger and Wallet cache.
    """
    # admin_emails = [email for name, email in settings.ADMINS]

    admin_emails = list(settings.ADMINS)

    if not admin_emails:
        admin_emails = [settings.DEFAULT_FROM_EMAIL]

    subject = f"🚨 URGENT: Financial Reconciliation Mismatch Detected - {timezone.now().date()}"

    mismatch_report = ""
    for item in mismatches:
        mismatch_report += (
            f"User: {item['user']}\n"
            f"  - Ledger Total: {item['ledger']}\n"
            f"  - Wallet Cache: {item['cache']}\n"
            f"  - Difference:   {item['diff']}\n"
            f"-------------------------------------\n"
        )

    message = (
        f"The daily reconciliation task found {len(mismatches)} account(s) with balance drift.\n\n"
        f"Mismatched Accounts:\n"
        f"{mismatch_report}\n"
        f"Action Required: Check the LedgerEntry logs for these users to identify the bug."
    )

    send_mail(
        subject=subject,
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=admin_emails,
        fail_silently=False,
    )

@stashed_task
def reconcile_financial_integrity():
    """
    THE WATCHDOG: Re-calculates every wallet balance from the Ledger
    and compares it to the cached Wallet.current_balance.
    Uses database-level aggregation to detect 'Drift'.
    """
    mismatches = []
    wallets = Wallet.objects.select_related('user').all().iterator()

    for wallet in wallets:
        stats = LedgerEntry.objects.filter(wallet=wallet).aggregate(
            balance=Sum(
                models.Case(
                    models.When(direction='credit', then=models.F('amount')),
                    models.When(direction='debit', then=-models.F('amount')),
                    default=Decimal('0.00'),
                    output_field=models.DecimalField()
                )
            )
        )

        calculated_truth = stats['balance'] or Decimal('0.00')

        if calculated_truth != wallet.current_balance:
            mismatches.append({
                "user": wallet.user.email,
                "ledger": str(calculated_truth),
                "cache": str(wallet.current_balance),
                "diff": str(calculated_truth - wallet.current_balance)
            })

            logger.critical(f"BALANCE_DRIFT: User {wallet.user.email} | Expected: {calculated_truth} | Found: {wallet.current_balance}")

    if mismatches:
        send_reconciliation_alert_to_admins(mismatches)


@stashed_task
def clear_old_idempotency_keys():
    """
    Run this daily via Celery Beat to keep the DB lean.
    Maintains DB performance by pruning old keys.
    """
    expiry_limit = timezone.now() - timedelta(hours=48)
    count, _ = IdempotencyKey.objects.filter(created_at__lt=expiry_limit).delete()
    return f"Purged {count} idempotency keys."




# # accounts/tasks.py

# import requests
# from decimal import Decimal
# from django.conf import settings
# from django.core.mail import send_mail
# from django.template.loader import render_to_string
# from django.contrib.sites.models import Site
# from django.urls import NoReverseMatch, reverse
# from django.db import models, transaction
# from django.utils import timezone
# from datetime import timedelta
# from django.db.models import Sum
# from celery import shared_task
# from .models import SavingsGroup, Contribution, PayoutOrder, SavingsGoal, Wallet, LedgerEntry, IdempotencyKey
# from .services import LedgerService
# import logging


# logger = logging.getLogger('accounts.finance')


# # Dawurobo API constants
# DAWUROBO_BASE = "https://devs.sms.api.dawurobo.com/v1/otp"

# HEADERS = {
#     "accept": "application/json",
#     "x-api-key": settings.DAWUROBO_API_KEY,
#     "x-access-token": settings.DAWUROBO_ACCESS_TOKEN,
#     "Content-Type": "application/json"
# }


# @shared_task(bind=True, max_retries=3, default_retry_delay=60)
# def send_dawurobo_otp_async(self, phone_number: str):
#     """
#     GOOGLE FIX: OTP sending MUST be async.
#     A 30s timeout in a request-response cycle is a DoS risk.
#     """
#     clean_number = phone_number.replace("+", "").replace(" ", "")
#     payload = {
#         "senderid": settings.DAWUROBO_SENDER_ID,
#         "number": clean_number,
#         "messagetemplate": "Your SnappX code: %OTPCODE%. Expires in %EXPIRY% min.",
#         "expiry": 10,
#         "length": 6,
#         "type": "NUMERIC"
#     }

#     try:
#         response = requests.post(f"{DAWUROBO_BASE}/generate", json=payload, headers=HEADERS, timeout=15)
#         response.raise_for_status()
#         return {"success": True}
#     except Exception as exc:
#         logger.error(f"OTP SEND FAIL: {phone_number} - {exc}")
#         raise self.retry(exc=exc)


# def verify_and_invalidate_otp_sync(phone_number: str, code: str) -> bool:
#     """
#     Secure synchronous verification + immediate invalidation.
#     Used for signup verification and password reset.
#     """
#     clean_number = phone_number.replace("+", "").replace(" ", "")
#     verify_payload = {"otpcode": code.upper(), "number": clean_number}

#     try:
#         verify_resp = requests.post(
#             f"{DAWUROBO_BASE}/verify",
#             json=verify_payload,
#             headers=HEADERS,
#             timeout=10
#         )

#         success = verify_resp.status_code == 200 and "success" in verify_resp.text.lower()

#         if success:
#             # Immediately invalidate to prevent reuse
#             requests.post(
#                 f"{DAWUROBO_BASE}/invalidate",
#                 json={"number": clean_number},
#                 headers=HEADERS,
#                 timeout=3
#             )
#             print(f"OTP verified and invalidated for {phone_number}")
#             return True
#         else:
#             print(f"Invalid OTP attempt: {verify_resp.text}")
#             return False

#     except Exception as e:
#         print(f"OTP verify/invalidate failed: {e}")
#         return False


# @shared_task
# def send_group_join_request_email_async(request_id: int):
#     """
#     Celery task to send an email to the Group Admin notifying them
#     of a new join request.
#     """
#     try:
#         from .models import GroupJoinRequest

#         join_request = GroupJoinRequest.objects.select_related(
#             'group__admin', 'user__profile'
#         ).get(pk=request_id)

#     except GroupJoinRequest.DoesNotExist:
#         print(f"ERROR: GroupJoinRequest with ID {request_id} not found.")
#         return False

#     group = join_request.group
#     admin_user = group.admin
#     requester_name = join_request.user.profile.full_name

#     # Construct the Deep Link URL
#     current_site = Site.objects.get_current()
#     protocol = "http" if settings.DEBUG else "https"

#     try:
#         relative_url = reverse('group-requests-list', kwargs={'group_id': group.id})
#     except NoReverseMatch:
#         print("URL Reverse Match Error: Check URL configuration for 'group-requests-list'")
#         return False

#     full_review_url = f"{protocol}://{current_site.domain}{relative_url}"

#     # Render Email Content
#     context = {
#         'admin_name': admin_user.profile.full_name,
#         'group_name': group.group_name,
#         'requester_name': requester_name,
#         'review_url': full_review_url,
#     }

#     email_html_content = render_to_string('emails/new_join_request.html', context)
#     email_text_content = f"A new user, {requester_name}, has requested to join your group: {group.group_name}. Review the request here: {full_review_url}"

#     # Send Email
#     try:
#         send_mail(
#             subject=f"🚀 New Join Request for '{group.group_name}'",
#             message=email_text_content,
#             from_email=settings.DEFAULT_FROM_EMAIL,
#             recipient_list=[admin_user.email],
#             html_message=email_html_content,
#             fail_silently=False,
#         )
#         return True
#     except Exception as e:
#         print(f"EMAIL SEND ERROR for Group ID {group.id}: {e}")
#         return False


# @shared_task
# def send_group_join_response_email_async(request_id: int, action: str):
#     """
#     Celery task to send an email to the applicant (user) notifying them
#     that their join request has been approved or rejected by the admin.
#     """
#     try:
#         from .models import GroupJoinRequest

#         join_request = GroupJoinRequest.objects.select_related(
#             'group__admin', 'user__profile'
#         ).get(pk=request_id)

#     except GroupJoinRequest.DoesNotExist:
#         print(f"ERROR: GroupJoinRequest with ID {request_id} not found for response.")
#         return False

#     group = join_request.group
#     applicant_user = join_request.user
#     applicant_name = applicant_user.profile.full_name

#     clean_group_name = group.group_name.strip('*').strip()

#     # Construct the Deep Link URL
#     current_site = Site.objects.get_current()
#     protocol = "http" if settings.DEBUG else "https"

#     try:
#         relative_url = reverse('group-detail', kwargs={'id': group.id})
#     except NoReverseMatch:
#         print("URL Reverse Match Error: Check URL configuration for 'group-detail'")
#         relative_url = reverse('group_list_all')

#     full_group_url = f"{protocol}://{current_site.domain}{relative_url}"

#     # Determine Subject and Template based on action
#     if action == 'approved':
#         subject = f"🎉 Welcome! You've Joined '{clean_group_name}'"
#         template_name = 'emails/join_request_approved.html'
#         message_prefix = "Congratulations! Your request to join"
#     elif action == 'rejected':
#         subject = f"😔 Update: Request to Join '{clean_group_name}'"
#         template_name = 'emails/join_request_rejected.html'
#         message_prefix = "Unfortunately, your request to join"
#     else:
#         print(f"ERROR: Invalid action '{action}' passed to email task.")
#         return False

#     # Render Email Content
#     context = {
#         'applicant_name': applicant_name,
#         'group_name': group.group_name,
#         'group_url': full_group_url,
#         'action': action,
#         'admin_name': group.admin.profile.full_name,
#     }

#     email_html_content = render_to_string(template_name, context)
#     email_text_content = (
#     f"Update for group '{clean_group_name}': Your request was {action}. "
#     f"Log in to view details: {full_group_url}"
# )

#     # Send Email
#     try:
#         send_mail(
#             subject=subject,
#             message=email_text_content,
#             from_email=settings.DEFAULT_FROM_EMAIL,
#             recipient_list=[applicant_user.email],
#             html_message=email_html_content,
#             fail_silently=False,
#         )
#         print(f"Group join response '{action}' email sent to {applicant_user.email}")
#         return True
#     except Exception as e:
#         print(f"EMAIL SEND ERROR for Request ID {request_id}: {e}")
#         return False


# @shared_task(bind=True, max_retries=3)
# def process_daily_payouts(self):
#     """
#     High-performance daily orchestrator for group disbursements.
#     Uses database-level filtering and iterators to handle 100k+ groups efficiently.
#     """
#     today = timezone.now().date()
#     queryset = SavingsGroup.objects.filter(
#         status='active',
#         start_date__lte=today
#     ).only('id', 'start_date', 'payout_interval_days').iterator(chunk_size=1000)

#     dispatched_count = 0

#     for group in queryset:
#         days_since_start = (today - group.start_date).days

#         if days_since_start > 0 and days_since_start % group.payout_interval_days == 0:
#             execute_group_payout.delay(group.id)
#             dispatched_count += 1

#     return f"Orchestrator finished. Dispatched {dispatched_count} payout tasks."

# @shared_task(bind=True, max_retries=2)
# def execute_group_payout(self, group_id):
#     """
#     Atomic Payout Logic.
#     Ensures 'Conservation of Money' and 'At-Most-Once' execution.
#     """
#     try:
#         with transaction.atomic():
#             group = SavingsGroup.objects.select_for_update().get(id=group_id)
#             current_cycle = group.current_cycle_number

#             payout_ref = f"PAYOUT-GRP{group.id}-C{current_cycle}"
#             if LedgerEntry.objects.filter(reference__contains=payout_ref).exists():
#                 logger.warning(f"Aborting: Cycle {current_cycle} for Group {group_id} already disbursed.")
#                 return

#             verified_count = Contribution.objects.filter(
#                 membership__group=group, cycle_number=current_cycle, is_verified=True
#             ).count()

#             if verified_count < group.expected_members:
#                 logger.error(f"Payout Failed: Insufficient funds for Group {group_id} Cycle {current_cycle}")
#                 return

#             position = ((current_cycle - 1) % group.expected_members) + 1
#             payout_order = PayoutOrder.objects.select_related('membership__user').get(
#                 group=group, position=position
#             )
#             beneficiary = payout_order.membership.user

#             total_pot = group.total_pot_per_cycle

#             LedgerService.transfer(
#                 from_group=group,
#                 to_user=beneficiary,
#                 amount=total_pot,
#                 transaction_type='payout',
#                 description=f"Payout Cycle {current_cycle} to {beneficiary.profile.full_name}",
#                 reference=payout_ref,
#                 actor=group.admin,
#                 request_id=None
#             )

#             group.current_cycle_number += 1
#             if current_cycle >= group.expected_members:
#                 group.status = 'completed'
#             group.save()

#             transaction.on_commit(lambda: send_payout_notification_email_async.delay(
#                 beneficiary.id, group.id, current_cycle, str(total_pot)
#             ))

#     except Exception as exc:
#         logger.critical(f"CRITICAL PAYOUT ERROR: Group {group_id} - {exc}")
#         raise self.retry(exc=exc)

# @shared_task
# def send_payout_notification_email_async(
#     beneficiary_id: int,
#     group_id: int,
#     cycle: int,
#     amount: str
# ) -> bool:
#     """
#     Celery task to send a payout notification email to the beneficiary.
#     """
#     try:
#         from .models import SavingsGroup, User
#         beneficiary = User.objects.select_related('profile').get(pk=beneficiary_id)
#         group = SavingsGroup.objects.get(pk=group_id)
#     except (User.DoesNotExist, SavingsGroup.DoesNotExist):
#         print(f"ERROR: User ID {beneficiary_id} or Group ID {group_id} not found.")
#         return False

#     beneficiary_name = beneficiary.profile.full_name
#     group_name = group.group_name.strip('*').strip()
#     momo_number = str(beneficiary.profile.momo_number)
#     formatted_amount = f"₵{Decimal(amount):,.2f}"

#     current_site = Site.objects.get_current()
#     protocol = "http" if settings.DEBUG else "https"
#     try:
#         relative_url = reverse('dashboard')
#     except NoReverseMatch:
#         print("URL Reverse Match Error: Check URL configuration for 'dashboard'")
#         relative_url = reverse('group-detail', kwargs={'id': group.id})
#     full_dashboard_url = f"{protocol}://{current_site.domain}{relative_url}"

#     # Render Email Content
#     context = {
#         'beneficiary_name': beneficiary_name,
#         'group_name': group_name,
#         'cycle': cycle,
#         'amount': formatted_amount,
#         'momo_number': momo_number,
#         'dashboard_url': full_dashboard_url,
#     }
#     template_name = 'emails/payout_notification.html'
#     subject = f"🎉 Payout Processed from '{group_name}'!"
#     email_html_content = render_to_string(template_name, context)
#     email_text_content = (
#         f"Congratulations, {beneficiary_name}! Your payout of {formatted_amount} "
#         f"for cycle {cycle} in '{group_name}' has been processed. "
#         f"It will be sent to your MoMo account: {momo_number}. "
#         f"View your dashboard: {full_dashboard_url}"
#     )

#     try:
#         send_mail(
#             subject=subject,
#             message=email_text_content,
#             from_email=settings.DEFAULT_FROM_EMAIL,
#             recipient_list=[beneficiary.email],
#             html_message=email_html_content,
#             fail_silently=False,
#         )
#         print(f"Payout notification email sent to {beneficiary.email} for Group ID {group.id}")
#         return True
#     except Exception as e:
#         print(f"EMAIL SEND ERROR for Beneficiary ID {beneficiary_id}: {e}")
#         return False

# @shared_task
# def send_goal_reminders():
#     """
#     Celery task to check all active savings goals and send reminder emails.
#     Includes precision handling for financial progress and safety checks.
#     """
#     now = timezone.now()
#     today = now.date()


#     goals = SavingsGoal.objects.annotate(
#         total_saved=Sum('contributions__amount')
#     ).filter(
#         total_saved__lt=models.F('target_amount')
#     ).select_related('user__profile')

#     for goal in goals:
#         current_saved = goal.total_saved or Decimal('0.00')
#         target = goal.target_amount

#         if not goal.is_due:
#             continue

#         if goal.last_reminded_at and (now - goal.last_reminded_at) < timedelta(days=1):
#             continue

#         if target > 0:
#             progress_decimal = (current_saved / target) * 100
#             progress_percentage = float(round(progress_decimal, 1))
#         else:
#             progress_percentage = 0.0

#         user = goal.user
#         user_name = user.profile.full_name or "there"

#         current_site = Site.objects.get_current()
#         protocol = "http" if settings.DEBUG else "https"

#         try:
#             dashboard_url = f"{protocol}://{current_site.domain}{reverse('goals-dashboard')}"
#         except NoReverseMatch:
#             dashboard_url = f"{protocol}://{current_site.domain}/dashboard/"

#         context = {
#             'user_name': user_name,
#             'goal_name': goal.name,
#             'contribution_amount': f"₵{goal.regular_contribution:,.2f}",
#             'frequency': goal.get_frequency_display(),
#             'current_saved': f"₵{current_saved:,.2f}",
#             'target_amount': f"₵{target:,.2f}",
#             'progress_percentage': progress_percentage,
#             'target_date': goal.target_date.strftime('%B %d, %Y'),
#             'dashboard_url': dashboard_url,
#         }

#         subject = f"⏰ Reminder: Your {goal.get_frequency_display()} contribution for '{goal.name}'"
#         html_content = render_to_string('emails/goal_reminder.html', context)

#         text_content = (
#             f"Hi {user_name},\n\n"
#             f"Friendly reminder to make your {goal.get_frequency_display()} contribution of "
#             f"₵{goal.regular_contribution:,.2f} towards your goal: \"{goal.name}\".\n\n"
#             f"Current Progress: {progress_percentage}% (₵{current_saved:,.2f} of ₵{target:,.2f})\n"
#             f"Target Date: {goal.target_date.strftime('%B %d, %Y')}\n\n"
#             f"Keep up the great work! Log in here to contribute: {dashboard_url}"
#         )

#         try:
#             send_mail(
#                 subject=subject,
#                 message=text_content,
#                 from_email=settings.DEFAULT_FROM_EMAIL,
#                 recipient_list=[user.email],
#                 html_message=html_content,
#                 fail_silently=False,
#             )

#             goal.last_reminded_at = now
#             goal.save(update_fields=['last_reminded_at'])

#             logger.info(f"Goal reminder sent to {user.email} for goal: {goal.name}")

#         except Exception as e:
#             logger.error(f"Failed to send goal reminder to {user.id}: {str(e)}")


# def send_reconciliation_alert_to_admins(mismatches):
#     """
#     Sends a high-priority email to system administrators when
#     financial drift is detected between the Ledger and Wallet cache.
#     """
#     # admin_emails = [email for name, email in settings.ADMINS]

#     admin_emails = list(settings.ADMINS)

#     if not admin_emails:
#         admin_emails = [settings.DEFAULT_FROM_EMAIL]

#     subject = f"🚨 URGENT: Financial Reconciliation Mismatch Detected - {timezone.now().date()}"

#     mismatch_report = ""
#     for item in mismatches:
#         mismatch_report += (
#             f"User: {item['user']}\n"
#             f"  - Ledger Total: {item['ledger']}\n"
#             f"  - Wallet Cache: {item['cache']}\n"
#             f"  - Difference:   {item['diff']}\n"
#             f"-------------------------------------\n"
#         )

#     message = (
#         f"The daily reconciliation task found {len(mismatches)} account(s) with balance drift.\n\n"
#         f"Mismatched Accounts:\n"
#         f"{mismatch_report}\n"
#         f"Action Required: Check the LedgerEntry logs for these users to identify the bug."
#     )

#     send_mail(
#         subject=subject,
#         message=message,
#         from_email=settings.DEFAULT_FROM_EMAIL,
#         recipient_list=admin_emails,
#         fail_silently=False,
#     )

# @shared_task
# def reconcile_financial_integrity():
#     """
#     THE WATCHDOG: Re-calculates every wallet balance from the Ledger
#     and compares it to the cached Wallet.current_balance.
#     Uses database-level aggregation to detect 'Drift'.
#     """
#     mismatches = []
#     wallets = Wallet.objects.select_related('user').all().iterator()

#     for wallet in wallets:
#         stats = LedgerEntry.objects.filter(wallet=wallet).aggregate(
#             balance=Sum(
#                 models.Case(
#                     models.When(direction='credit', then=models.F('amount')),
#                     models.When(direction='debit', then=-models.F('amount')),
#                     default=Decimal('0.00'),
#                     output_field=models.DecimalField()
#                 )
#             )
#         )

#         calculated_truth = stats['balance'] or Decimal('0.00')

#         if calculated_truth != wallet.current_balance:
#             mismatches.append({
#                 "user": wallet.user.email,
#                 "ledger": str(calculated_truth),
#                 "cache": str(wallet.current_balance),
#                 "diff": str(calculated_truth - wallet.current_balance)
#             })

#             logger.critical(f"BALANCE_DRIFT: User {wallet.user.email} | Expected: {calculated_truth} | Found: {wallet.current_balance}")

#     if mismatches:
#         send_reconciliation_alert_to_admins(mismatches)


# @shared_task
# def clear_old_idempotency_keys():
#     """
#     Run this daily via Celery Beat to keep the DB lean.
#     Maintains DB performance by pruning old keys.
#     """
#     expiry_limit = timezone.now() - timedelta(hours=48)
#     count, _ = IdempotencyKey.objects.filter(created_at__lt=expiry_limit).delete()
#     return f"Purged {count} idempotency keys."
