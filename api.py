"""FastAPI owns the agent, conversation history and database access."""
import logging
from threading import Lock
from typing import Annotated
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
import agent
import database

log = logging.getLogger(__name__)
app = FastAPI(title="Corner Kiosk API", version="1.0")
SessionId = Annotated[str, Header(alias="X-Amzn-Bedrock-AgentCore-Runtime-Session-Id", min_length=1, max_length=256)]


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=4000, strict=True)


class Conversation:
    def __init__(self):
        self.history = []
        self.lock = Lock()


# One worker for this teaching app. History lasts only for this server process.
sessions = {}


def conversation(session_id):
    if not session_id.strip():
        raise HTTPException(status_code=400, detail="A session ID is required.")
    return sessions.setdefault(session_id, Conversation())


@app.get("/ping")
def ping():
    return {"status": "Healthy"}


@app.get("/menu")
def menu():
    return database.MENU


@app.get("/order")
def current_order(session_id: SessionId):
    state = conversation(session_id)
    with state.lock:
        return database.get_order(session_id)


@app.post("/reset")
def reset(session_id: SessionId):
    state = conversation(session_id)
    with state.lock:
        order = database.save_order(session_id, [])
        state.history = []
    return {"order": order}


# /chat is the local REST API; /invocations exposes the same chat operation
# using AgentCore's default HTTP contract. No second backend server is needed.
@app.post("/chat")
@app.post("/invocations")
def chat(body: ChatRequest, session_id: SessionId):
    if not body.prompt.strip():
        raise HTTPException(status_code=422, detail="prompt cannot be blank.")
    state = conversation(session_id)
    with state.lock:
        try:
            result = agent.respond(body.prompt, session_id, state.history)
        except Exception:
            log.exception("Chat failed")
            raise HTTPException(status_code=502, detail="The agent could not complete the request. Check your saved draft before retrying.") from None
        state.history = result["history"]
    return {"answer": result["answer"], "order": result["order"],
            "steps": result["steps"], "session_id": session_id}
