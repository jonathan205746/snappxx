import pytest
from django.urls import reverse
from accounts.models import LedgerEntry

@pytest.mark.django_db
def test_request_id_propagates_to_ledger(auth_client, test_group, funded_wallet, membership):
    """
    Observability: The X-Request-ID header sent by the client MUST end up
    in the LedgerEntry for debugging purposes.
    """
    url = reverse('group-contribute', kwargs={'group_id': test_group.id})
    client_trace_id = "trace-google-abc-123"

    response = auth_client.post(
        url, {},
        HTTP_X_IDEMPOTENCY_KEY="obs-key-1",
        HTTP_X_REQUEST_ID=client_trace_id
    )

    assert response.status_code == 201

    entry = LedgerEntry.objects.filter(reference__startswith="CONTRIB-").last()

    assert entry is not None
    assert entry.request_id == client_trace_id
