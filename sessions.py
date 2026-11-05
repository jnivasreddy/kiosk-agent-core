"""Customer identity and chat ownership. Messages stay in history.py."""
import os
from fastapi import HTTPException
from authentication import verify_login
from history import get_redis


def customer_for_session(session_id, access_token):
    if not session_id.strip():
        raise HTTPException(400, 'A session ID is required.')
    # Explicit compatibility mode for the old anonymous classroom notebook only.
    if os.getenv('KIOSK_AUTH_MODE', 'cognito') == 'session_demo':
        return session_id
    customer_id = verify_login(access_token)
    redis = get_redis()
    if redis is None:
        raise HTTPException(503, 'Redis is required for authenticated session ownership.')
    key = 'kiosk:session:' + session_id
    # Atomic first-use binding. Retain ownership even when message history expires.
    redis.set(key, customer_id, nx=True)
    if redis.get(key) != customer_id:
        raise HTTPException(403, 'This conversation belongs to another customer.')
    return customer_id
