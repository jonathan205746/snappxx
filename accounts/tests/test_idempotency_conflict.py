import pytest
from django.urls import reverse
from rest_framework import status

@pytest.mark.django_db
def test_idempotency_rejects_payload_mismatch(auth_client, test_group, membership, funded_wallet):
    """
    Security Test: Attempting to reuse an Idempotency Key with DIFFERENT
    request data must result in a 409 Conflict.
    """
    url = reverse('group-contribute', kwargs={'group_id': test_group.id})
    key = "unique-key-conflict-test"

    # First Valid Request (Amount = 100)
    data_1 = {"amount": 100.00}
    response_1 = auth_client.post(url, data_1, HTTP_X_IDEMPOTENCY_KEY=key)
    assert response_1.status_code == status.HTTP_201_CREATED

    # Attack: Reuse key but try to change parameters
    data_2 = {"amount": 5000.00}
    response_2 = auth_client.post(url, data_2, HTTP_X_IDEMPOTENCY_KEY=key)

    # Expect 409 Conflict
    assert response_2.status_code == status.HTTP_409_CONFLICT
    assert "Idempotency key conflict" in str(response_2.data)
