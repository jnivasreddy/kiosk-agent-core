"""Redis serialization and failures, without touching the configured service."""
import unittest
import os
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from redis.exceptions import ConnectionError

import api
import history


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.mode = patch.dict(os.environ, {"KIOSK_AUTH_MODE": "session_demo"})
        self.mode.start()
        self.addCleanup(self.mode.stop)

    def test_redis_round_trip_preserves_tool_messages(self):
        redis = MagicMock()
        messages = [
            HumanMessage(content="Add coffee"),
            AIMessage(content="", tool_calls=[
                {"name": "get_menu", "args": {}, "id": "menu-1"}]),
            ToolMessage(content="[]", tool_call_id="menu-1"),
        ]
        with patch.object(history, "get_redis", return_value=redis):
            history.save_history("alice", messages)
            key, serialized = redis.set.call_args.args
            self.assertEqual(key, "kiosk:history:alice")
            self.assertEqual(redis.set.call_args.kwargs["ex"], 86400)
            redis.get.return_value = serialized
            restored = history.load_history("alice")
        self.assertEqual(restored, messages)

    def test_redis_failure_does_not_fall_back_to_empty_history(self):
        redis = MagicMock()
        redis.get.side_effect = ConnectionError("private connection detail")
        redis.lock.return_value.acquire.return_value = True
        with patch.object(history, "get_redis", return_value=redis):
            with TestClient(api.app) as client:
                response = client.post("/chat", json={"prompt": "hi"}, headers={
                    "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": "alice"})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private connection detail", response.text)

    def test_overlapping_turn_returns_conflict(self):
        redis = MagicMock()
        redis.lock.return_value.acquire.return_value = False
        with patch.object(history, "get_redis", return_value=redis):
            with TestClient(api.app) as client:
                response = client.post("/chat", json={"prompt": "hi"}, headers={
                    "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": "alice"})
        self.assertEqual(response.status_code, 409)
