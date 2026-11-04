"""FastAPI owns the agent, conversation history and database access."""
import logging
from typing import Annotated
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from pydantic import BaseModel, ConfigDict, Field
import agent
import database
from history import load_history, save_history, conversation_lock, ConversationBusy

log = logging.getLogger(__name__)
app = FastAPI(title="Corner Kiosk API", version="1.0")
SessionId = Annotated[str, Header(alias="X-Amzn-Bedrock-AgentCore-Runtime-Session-Id", min_length=1, max_length=256)]


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1, max_length=4000, strict=True)


def check_session(session_id):
    if not session_id.strip():
        raise HTTPException(status_code=400, detail="A session ID is required.")


@app.exception_handler(RedisError)
def redis_unavailable(request, exc):
    return JSONResponse(status_code=503, content={"detail": "Conversation storage is unavailable. Check your saved draft before retrying."})


@app.exception_handler(ConversationBusy)
def conversation_busy(request, exc):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.get("/ping")
def ping():
    return {"status": "Healthy"}


@app.get("/menu")
def menu():
    return database.MENU


@app.get("/order")
def current_order(session_id: SessionId):
    check_session(session_id)
    with conversation_lock(session_id):
        return database.get_order(session_id)


@app.post("/reset")
def reset(session_id: SessionId):
    check_session(session_id)
    with conversation_lock(session_id):
        order = database.save_order(session_id, [])
        save_history(session_id, [])
    return {"order": order}


# /chat is the local REST API; /invocations exposes the same chat operation
# using AgentCore's default HTTP contract. No second backend server is needed.
@app.post("/chat")
@app.post("/invocations")
def chat(body: ChatRequest, session_id: SessionId):
    if not body.prompt.strip():
        raise HTTPException(status_code=422, detail="prompt cannot be blank.")
    check_session(session_id)
    with conversation_lock(session_id):
        history = load_history(session_id)
        try:
            # Session-only demo until verified customer login is configured.
            customer_id = session_id
            result = agent.respond(body.prompt, customer_id, history)
        except Exception:
            log.exception("Chat failed")
            raise HTTPException(status_code=502, detail="The agent could not complete the request. Check your saved draft before retrying.") from None
        save_history(session_id, result["history"])
    return {"answer": result["answer"], "order": result["order"],
            "steps": result["steps"], "session_id": session_id}
