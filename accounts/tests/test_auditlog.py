import pytest
from auditlog.models import LogEntry
from accounts.models import SavingsGroup
from django.contrib.contenttypes.models import ContentType

@pytest.mark.django_db
def test_auditlog_records_group_update(test_user):
    """
    Verify model changes create audit logs.
    """
    group = SavingsGroup.objects.create(
        admin=test_user, name="Audit Test", group_name="audit-group",
        contribution_amount=100, frequency='daily', expected_members=2,
        status='pending', payout_timeline_days=30
    )
    group.status = 'active'
    group.save()
    # Assert log exists
    logs = LogEntry.objects.filter(object_id=group.id, content_type_id=ContentType.objects.get_for_model(SavingsGroup).id)
    assert logs.exists()
    log = logs.first()
    assert log.action == LogEntry.Action.UPDATE
    assert 'status' in log.changes
