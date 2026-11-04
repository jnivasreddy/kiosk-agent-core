"""Conversation messages in Redis; local memory when REDIS_URL is absent."""
from contextlib import contextmanager
from functools import lru_cache
import json
import os
from threading import Lock

from langchain_core.messages import messages_from_dict, messages_to_dict
from redis import Redis

# Local fallback is for one-process demos only.
local_history = {}
local_locks = {}
HISTORY_TTL = 86400  # One day after the last saved turn.


@lru_cache(maxsize=1)
def get_redis():
    url = os.getenv("REDIS_URL")
    if not url:
        return None
    return Redis.from_url(url, decode_responses=True,
                          socket_connect_timeout=5, socket_timeout=5)


def load_history(session_id):
    redis = get_redis()
    if redis is None:
        return local_history.get(session_id, [])
    saved = redis.get(f"kiosk:history:{session_id}")
    return messages_from_dict(json.loads(saved)) if saved else []


def save_history(session_id, messages):
    redis = get_redis()
    if redis is None:
        local_history[session_id] = messages
        return
    saved = json.dumps(messages_to_dict(messages))
    redis.set(f"kiosk:history:{session_id}", saved, ex=HISTORY_TTL)


@contextmanager
def conversation_lock(session_id):
    """Keep two simultaneous turns from overwriting the same history."""
    redis = get_redis()
    if redis is None:
        with local_locks.setdefault(session_id, Lock()):
            yield
        return
    # Lease exceeds the current bounded model loop. No waiting behind long turns.
    lock = redis.lock(f"kiosk:lock:{session_id}", timeout=1800)
    if not lock.acquire(blocking=False):
        raise ConversationBusy("This conversation is busy. Try again shortly.")
    try:
        yield
    finally:
        lock.release()


class ConversationBusy(Exception):
    pass
