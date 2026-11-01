"""The complete model → tool → model loop, without an agent framework wrapper."""
from functools import lru_cache
import json
import logging
import os
from pathlib import Path
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from tools import create_tools
import database

load_dotenv(Path(__file__).parent / ".env")
log = logging.getLogger(__name__)
SYSTEM_PROMPT = """You are a kiosk ordering assistant.
Use the menu and order tools for real information. Never invent products or prices.
100 paise = INR 1. Ask for quantities when missing.
Read the current order before edits. set_order replaces the entire draft:
preserve any items the customer wants to keep. Use [] only when asked to clear it.
Confirm a change only after a successful set_order result in this turn.
After a tool error, explain the problem or correct the arguments; do not claim success.
Orders here are saved drafts. You cannot place orders or take payment.
"""


@lru_cache(maxsize=1)
def get_model():
    key = os.getenv("OPEN_ROUTER_API_KEY")
    if not key:
        raise ValueError("Add OPEN_ROUTER_API_KEY to this folder's .env file.")
    return ChatOpenAI(model=os.getenv("OPEN_ROUTER_MODEL", "google/gemini-2.5-flash"),
                      api_key=key, base_url="https://openrouter.ai/api/v1",
                      temperature=0, timeout=45, max_retries=1)


def respond(query, customer_id, history):
    """One chat turn. Return updated history so the caller can retain context."""
    if not isinstance(query, str) or not query.strip() or len(query) > 4000:
        raise ValueError("Enter a message of 1–4000 characters.")
    tools = create_tools(customer_id)
    available = {tool.name: tool for tool in tools}
    model = get_model().bind_tools(tools)
    messages = [SystemMessage(content=SYSTEM_PROMPT), *history, HumanMessage(content=query)]
    steps = []
    for _ in range(8):
        log.info("Calling model")
        response = model.invoke(messages)
        messages.append(response)
        if not response.tool_calls:
            return {"answer": response.content, "history": messages[1:],
                    "order": database.get_order(customer_id), "steps": steps}
        for call in response.tool_calls:
            log.info("Calling tool: %s arguments=%s", call["name"], call["args"])
            try:
                if call["name"] not in available:
                    raise ValueError("Unknown tool. Use one of the supplied tools.")
                result = available[call["name"]].invoke(call["args"])
            except ValueError as exc:
                # Validation problems become evidence the model can respond to.
                result = {"error": str(exc)}
            steps.append({"tool": call["name"], "arguments": call["args"], "result": result})
            messages.append(ToolMessage(content=json.dumps(result), tool_call_id=call["id"]))
    raise RuntimeError("Too many model rounds. Please try a simpler request.")
