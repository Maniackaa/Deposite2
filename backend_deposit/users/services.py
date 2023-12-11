from uuid import uuid4

from django.conf import settings
from django.utils import timezone


def create_jwt_payload(user) -> dict:
    now = timezone.now()
    expires = int(now.timestamp() + settings.ACCESS_TOKEN_EXPIRE_SECONDS)
    payload = {
        "iat": int(now.timestamp()),
        "exp": expires,
        "jti": str(uuid4),
        "username": user.username,
    }
    return payload


def create_jwt_refresh_payload(user) -> dict:
    now = timezone.now()
    expires = int(now.timestamp() + settings.REFRESH_TOKEN_EXPIRE_SECONDS)
    payload = {
        "iat": int(now.timestamp()),
        "exp": expires,
        "jti": str(uuid4),
    }
    return payload
