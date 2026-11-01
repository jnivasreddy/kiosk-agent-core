"""AgentCore adapter for our existing agent. Running this file starts LOCAL HTTP only."""
import logging
import os
from threading import Lock

# Deployment packages are not a writable order store. This demo uses /tmp.
os.environ.setdefault("KIOSK_DB_PATH", "/tmp/kiosk-agentcore.sqlite3")

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from agent import respond

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
app = BedrockAgentCoreApp()
histories = {}
turn_lock = Lock()


@app.entrypoint
def invoke(payload, context):
    """Request: {prompt: str}. Identity comes from Runtime context, not the body."""
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object with a prompt.")
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000:
        raise ValueError("prompt must contain 1–4000 characters.")
    session_id = getattr(context, "session_id", None)
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("A Runtime session ID is required.")
    # Reuse one session ID across turns. Sessions are NOT verified customer logins.
    with turn_lock:
        result = respond(prompt, session_id, histories.get(session_id, []))
        histories[session_id] = result["history"]
    # LangChain Message objects remain private; only JSON data crosses the API.
    return {"answer": result["answer"], "order": result["order"],
            "steps": result["steps"], "session_id": session_id}


if __name__ == "__main__":
    app.run()
