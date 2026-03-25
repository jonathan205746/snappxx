import jwt
import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from datetime import datetime, timezone as dt_timezone

@pytest.mark.django_db
def test_token_refresh_with_remember_me_extension(auth_client, test_user):
    """
    Verify refresh token lifetime extends with remember_me.
    """
    # Ensure user is verified (required for login)
    test_user.is_verified = True
    test_user.save()

    url = reverse('login')

    # Long-lived token (remember_me=True)
    resp_long = auth_client.post(url, {
        "login_field": test_user.email,
        "password": "password123",
        "remember_me": True
    })
    assert resp_long.status_code == status.HTTP_200_OK
    refresh_long = resp_long.data['refresh']
    payload_long = jwt.decode(refresh_long, options={"verify_signature": False})

    exp_long = datetime.fromtimestamp(payload_long['exp'], tz=dt_timezone.utc)

    # Short-lived token (remember_me=False)
    resp_short = auth_client.post(url, {
        "login_field": test_user.email,
        "password": "password123",
        "remember_me": False
    })
    assert resp_short.status_code == status.HTTP_200_OK
    refresh_short = resp_short.data['refresh']
    payload_short = jwt.decode(refresh_short, options={"verify_signature": False})

    exp_short = datetime.fromtimestamp(payload_short['exp'], tz=dt_timezone.utc)

    now = timezone.now()

    lifetime_long = exp_long - now
    lifetime_short = exp_short - now

    assert lifetime_long > lifetime_short * 20

@pytest.mark.django_db
def test_protected_view_rejects_invalid_token(api_client, test_group):
    """
    Edge: Tampered JWT should return 401.
    """
    url = reverse('group-detail', kwargs={'id': test_group.id})
    invalid_token = jwt.encode({"user_id": 999999}, "fake_secret_key", algorithm="HS256")
    api_client.credentials(HTTP_AUTHORIZATION=f'Bearer {invalid_token}')
    response = api_client.get(url)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
def test_login_with_phone_number_variant(api_client, test_user):
    """
    Verify login works with phone (momo_number) as login_field.
    """
    # Ensure user is verified and password is set
    test_user.is_verified = True
    test_user.set_password("password123")
    test_user.save()

    url = reverse('login')
    resp = api_client.post(url, {
        "login_field": test_user.profile.momo_number,
        "password": "password123",
        "remember_me": False
    })
    assert resp.status_code == status.HTTP_200_OK
    assert 'access' in resp.data
    assert 'refresh' in resp.data
