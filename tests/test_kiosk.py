"""HTTP + Streamlit checks: scripted model, real SQLite, no network calls."""
import os
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent
import api
import database
import history
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"


class ScriptedModel:
    def __init__(self, items):
        self.replies = iter([
            AIMessage(content="", tool_calls=[{"name": "get_current_order", "args": {}, "id": "read"}]),
            AIMessage(content="", tool_calls=[{"name": "set_order", "args": {"items": items}, "id": "write"}]),
            AIMessage(content="Your draft is saved; no payment was taken.")])
    def bind_tools(self, tools):
        return self
    def invoke(self, messages):
        return next(self.replies)


class KioskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = patch.object(database, "DB_PATH", Path(self.temp.name) / "test.sqlite3")
        self.path.start()
        history.local_history.clear()
        history.local_locks.clear()
        self.redis = patch.object(history, "get_redis", return_value=None)
        self.redis.start()
        self.client = TestClient(api.app)

    def tearDown(self):
        self.client.close()
        history.local_history.clear()
        history.local_locks.clear()
        self.redis.stop()
        self.path.stop()
        self.temp.cleanup()

    def test_invalid_write_preserves_draft(self):
        database.save_order("alice", [{"product_id": "cold_coffee", "quantity": 1}])
        for bad in ([{"product_id": "missing", "quantity": 1}],
                    [{"product_id": "cold_coffee", "quantity": True}],
                    [{"product_id": "cold_coffee", "quantity": 21}],
                    [{"product_id": "cold_coffee", "quantity": 1}] * 2):
            with self.assertRaises(ValueError):
                database.save_order("alice", bad)
        self.assertEqual(database.get_order("alice")["total_paise"], 12000)

    def test_http_chat_preserves_items_and_isolates_sessions(self):
        database.save_order("alice", [{"product_id": "veg_sandwich", "quantity": 1}])
        items = [{"product_id": "veg_sandwich", "quantity": 1}, {"product_id": "cold_coffee", "quantity": 2}]
        with patch.object(agent, "get_model", return_value=ScriptedModel(items)):
            response = self.client.post("/chat", headers={HEADER: "alice"}, json={"prompt": "Keep sandwich and add two coffees."})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["order"]["total_paise"], 34000)
        self.assertNotIn("history", result)
        self.assertEqual(self.client.get("/order", headers={HEADER: "bob"}).json()["items"], [])
        self.assertTrue(history.load_history("alice"))

    def test_history_stays_on_backend(self):
        with patch.object(agent, "get_model", return_value=ScriptedModel([
                {"product_id": "cold_coffee", "quantity": 1}])):
            self.client.post("/chat", headers={HEADER: "alice"}, json={"prompt": "Save one coffee."})
        class ReadModel:
            def bind_tools(self, tools):
                return self
            def invoke(self, messages):
                self.seen = messages
                return AIMessage(content="Your draft remains saved.")
        model = ReadModel()
        with patch.object(agent, "get_model", return_value=model):
            result = self.client.post("/chat", headers={HEADER: "alice"}, json={"prompt": "Thanks"})
        self.assertEqual(result.status_code, 200)
        self.assertGreater(len(model.seen), 3)

    def test_reset_clears_only_current_session(self):
        for name in ("alice", "bob"):
            database.save_order(name, [{"product_id": "cold_coffee", "quantity": 1}])
            history.save_history(name, [AIMessage(content="old")])
        self.assertEqual(self.client.post("/reset", headers={HEADER: "alice"}).status_code, 200)
        self.assertEqual(database.get_order("alice")["items"], [])
        self.assertEqual(history.load_history("alice"), [])
        self.assertEqual(database.get_order("bob")["total_paise"], 12000)

    def test_validation_and_agentcore_path(self):
        self.assertEqual(self.client.get("/ping").json(), {"status": "Healthy"})
        self.assertEqual(len(self.client.get("/menu").json()), 4)
        self.assertEqual(self.client.get("/order").status_code, 422)
        for body in ({}, {"prompt": " "}, {"prompt": 123}, {"prompt": "hi", "customer_id": "bob"}):
            self.assertEqual(self.client.post("/chat", headers={HEADER: "alice"}, json=body).status_code, 422)
        with patch.object(agent, "get_model", return_value=ScriptedModel([
                {"product_id": "cold_coffee", "quantity": 1}])):
            result = self.client.post("/invocations", headers={HEADER: "alice"}, json={"prompt": "Save one coffee."})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["order"]["total_paise"], 12000)

    def test_provider_error_returns_safe_http_error(self):
        with patch.object(agent, "get_model", side_effect=RuntimeError("private-provider-detail")):
            with self.assertLogs("api", level="ERROR"):
                response = self.client.post("/chat", headers={HEADER: "alice"}, json={"prompt": "hi"})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private-provider-detail", response.text)
        self.assertEqual(history.load_history("alice"), [])

    @unittest.skipUnless(importlib.util.find_spec("streamlit"), "Install requirements.txt for UI checks")
    def test_streamlit_uses_http_for_chat_and_reset(self):
        from streamlit.testing.v1 import AppTest
        def request(method, url, headers, timeout, **kwargs):
            response = self.client.request(method, urlsplit(url).path, headers=headers, **kwargs)
            return SimpleNamespace(ok=response.is_success, json=response.json)
        with patch("requests.request", side_effect=request):
            ui = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
            self.assertFalse(ui.exception)
            session = ui.session_state["session_id"]
            with patch.object(agent, "get_model", return_value=ScriptedModel([
                    {"product_id": "cold_coffee", "quantity": 1}])):
                ui.chat_input[0].set_value("Save one coffee.").run()
            self.assertFalse(ui.exception)
            self.assertEqual(database.get_order(session)["total_paise"], 12000)
            self.assertEqual(len(ui.session_state["conversation"]), 2)
            ui.button[0].click().run()
            self.assertFalse(ui.exception)
            self.assertEqual(database.get_order(session)["items"], [])

    @unittest.skipUnless(importlib.util.find_spec("streamlit"), "Install requirements.txt for UI checks")
    def test_streamlit_explains_missing_backend(self):
        import requests
        from streamlit.testing.v1 import AppTest
        with patch("requests.request", side_effect=requests.ConnectionError()):
            ui = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
        self.assertFalse(ui.exception)
        self.assertIn("Start FastAPI", ui.error[0].value)


if __name__ == "__main__":
    unittest.main()
