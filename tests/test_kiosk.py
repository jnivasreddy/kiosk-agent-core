"""No AWS calls or model requests. Real SQLite with a scripted model."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent
import database
from langchain_core.messages import AIMessage


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

    def tearDown(self):
        self.path.stop()
        self.temp.cleanup()

    def test_loop_preserves_existing_item(self):
        database.save_order("alice", [{"product_id": "veg_sandwich", "quantity": 1}])
        items = [{"product_id": "veg_sandwich", "quantity": 1}, {"product_id": "cold_coffee", "quantity": 2}]
        with patch.object(agent, "get_model", return_value=ScriptedModel(items)):
            result = agent.respond("Keep my sandwich and add two coffees.", "alice", [])
        self.assertEqual(result["order"]["total_paise"], 34000)
        self.assertEqual([step["tool"] for step in result["steps"]], ["get_current_order", "set_order"])
        self.assertEqual(database.get_order("bob")["items"], [])

    def test_invalid_write_preserves_draft(self):
        database.save_order("alice", [{"product_id": "cold_coffee", "quantity": 1}])
        for bad in ([{"product_id": "missing", "quantity": 1}],
                    [{"product_id": "cold_coffee", "quantity": True}],
                    [{"product_id": "cold_coffee", "quantity": 21}],
                    [{"product_id": "cold_coffee", "quantity": 1}] * 2):
            with self.assertRaises(ValueError):
                database.save_order("alice", bad)
        self.assertEqual(database.get_order("alice")["total_paise"], 12000)

    @unittest.skipUnless(importlib.util.find_spec("bedrock_agentcore"), "Install requirements-agentcore.txt for adapter checks")
    def test_runtime_session_identity_history_and_json(self):
        import runtime
        runtime.histories.clear()
        session = "classroom-session-00000000-0000-0000-0000-000000000001"
        context = SimpleNamespace(session_id=session)
        with patch.object(agent, "get_model", return_value=ScriptedModel([
                {"product_id": "cold_coffee", "quantity": 1}])):
            result = runtime.invoke({"prompt": "Save one coffee.", "customer_id": "someone-else"}, context)
        self.assertEqual(result["order"]["total_paise"], 12000)
        self.assertEqual(database.get_order("someone-else")["items"], [])
        self.assertNotIn("history", result)
        self.assertTrue(runtime.histories[session])
        import json
        json.dumps(result)
        class ReadModel:
            def bind_tools(self, tools):
                return self
            def invoke(self, messages):
                self.seen = messages
                return AIMessage(content="Your earlier draft remains saved.")
        model = ReadModel()
        with patch.object(agent, "get_model", return_value=model):
            again = runtime.invoke({"prompt": "Thanks"}, context)
        self.assertGreater(len(model.seen), 3)
        self.assertEqual(again["order"]["total_paise"], 12000)
        self.assertEqual(database.get_order("different-session")["items"], [])
        runtime.histories.clear()

    @unittest.skipUnless(importlib.util.find_spec("bedrock_agentcore"), "Install requirements-agentcore.txt for adapter checks")
    def test_runtime_rejects_missing_context_and_bad_input(self):
        import runtime
        for payload in ({}, {"prompt": ""}, {"prompt": 123}, []):
            with self.assertRaises(ValueError):
                runtime.invoke(payload, SimpleNamespace(session_id="demo"))
        with self.assertRaises(ValueError):
            runtime.invoke({"prompt": "hello"}, SimpleNamespace(session_id=None))

    @unittest.skipUnless(importlib.util.find_spec("bedrock_agentcore"), "Install requirements-agentcore.txt for HTTP contract checks")
    def test_sdk_http_contract_without_starting_a_server(self):
        import runtime
        from starlette.testclient import TestClient
        runtime.histories.clear()
        session = "classroom-session-00000000-0000-0000-0000-000000000099"
        with TestClient(runtime.app) as client:
            self.assertEqual(client.get("/ping").status_code, 200)
            with patch.object(agent, "get_model", return_value=ScriptedModel([
                    {"product_id": "cold_coffee", "quantity": 1}])):
                response = client.post("/invocations", json={"prompt": "Save one coffee."},
                                       headers={"X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session})
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual(body["session_id"], session)
            self.assertEqual(body["order"]["total_paise"], 12000)
        runtime.histories.clear()


if __name__ == "__main__":
    unittest.main()
