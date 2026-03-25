import pytest
from django.urls import reverse
from rest_framework import status

@pytest.mark.django_db
def test_idempotency_header_is_mandatory(auth_client, test_group):
    """
    Security Gate: Ensure financial endpoints REJECT requests
    that lack the 'X-Idempotency-Key' header.
    """
    url = reverse('group-contribute', kwargs={'group_id': test_group.id})

    # Attempt POST without header
    response = auth_client.post(url, {})

    # Assert Rejection
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Idempotency key required" in str(response.data)

@pytest.mark.django_db
def test_idempotency_header_allows_valid_request(auth_client, test_group, funded_wallet, membership):
    """
    Smoke Test: Ensure the same endpoint accepts the request
    WHEN the header is present.
    """
    url = reverse('group-contribute', kwargs={'group_id': test_group.id})

    response = auth_client.post(
        url,
        {},
        HTTP_X_IDEMPOTENCY_KEY="valid-contract-key"
    )

    assert response.status_code == status.HTTP_201_CREATED
