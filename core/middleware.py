# core/middleware.py

import uuid
from threading import local
from django.http import JsonResponse

_thread_locals = local()

def get_current_request_id():
    return getattr(_thread_locals, 'request_id', None)

class RequestIDMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.headers.get('X-Request-ID', uuid.uuid4().hex)
        request.request_id = request_id
        _thread_locals.request_id = request_id

        response = self.get_response(request)

        response['X-Request-ID'] = request_id
        return response

class IdempotencyMiddleware:
    """
    Middleware to handle X-Idempotency-Key headers for state-changing requests.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method in ['POST', 'PUT', 'PATCH']:
            # Extract the key from headers
            idempotency_key = request.headers.get('X-Idempotency-Key')

            # Attach it to the request object so views/decorators can find it
            request.idempotency_key = idempotency_key

            _thread_locals.idempotency_key = idempotency_key

        response = self.get_response(request)
        return response
