# accounts/utils.py

import json
from functools import wraps
from hashlib import sha256

from django.db import transaction, IntegrityError
from django.core.serializers.json import DjangoJSONEncoder
from rest_framework.response import Response
from rest_framework import status

from .models import IdempotencyKey

def idempotent_request(view_func):
    @wraps(view_func)
    def wrapped_view(view_instance, request, *args, **kwargs):
        idempotency_key = request.headers.get('X-Idempotency-Key')

        if not idempotency_key:
            return Response(
                {
                    "error": "Idempotency key required",
                    "detail": "The 'X-Idempotency-Key' header is mandatory for this money-moving operation."
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        current_user = request.user if request.user.is_authenticated else None


        try:
            if (request.content_type and
                request.content_type.startswith('multipart/form-data')):
                hashable_data = {}
                for key, value in request.data.items():
                    if hasattr(value, 'read') or (
                        isinstance(value, list) and
                        any(hasattr(v, 'read') for v in value if v is not None)
                    ):
                        continue
                    hashable_data[key] = value
            else:
                # JSON or regular form data
                hashable_data = request.data

            payload_json = json.dumps(
                hashable_data,
                sort_keys=True,
                cls=DjangoJSONEncoder,
                default=str  # extra safety net
            )
            raw_string = f"{request.path}|{payload_json}"
            request_hash = sha256(raw_string.encode()).hexdigest()

        except (TypeError, ValueError, AttributeError):
            request_hash = sha256(request.path.encode()).hexdigest()

        existing_record = IdempotencyKey.objects.filter(
            user=current_user,
            key=idempotency_key
        ).first()

        if existing_record:
            if existing_record.request_hash != request_hash:
                return Response(
                    {"error": "Idempotency key conflict"},
                    status=status.HTTP_409_CONFLICT
                )
            return Response(existing_record.response_body, status=existing_record.response_code)

        try:
            with transaction.atomic():
                response = view_func(view_instance, request, *args, **kwargs)

                if response.status_code < 500:
                    sanitized_body = json.loads(
                        json.dumps(response.data, cls=DjangoJSONEncoder)
                    )

                    IdempotencyKey.objects.create(
                        user=current_user,
                        key=idempotency_key,
                        request_hash=request_hash,
                        response_code=response.status_code,
                        response_body=sanitized_body
                    )

                return response

        except IntegrityError:
            record = IdempotencyKey.objects.get(user=current_user, key=idempotency_key)
            return Response(record.response_body, status=record.response_code)
        except Exception as e:
            raise e

    return wrapped_view
