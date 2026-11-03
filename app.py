"""Streamlit is an HTTP client. Start the FastAPI backend first."""
import os
from pathlib import Path
from uuid import uuid4
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
API_URL = os.getenv("KIOSK_API_URL", "http://127.0.0.1:8080").rstrip("/")
st.set_page_config(page_title="Corner Kiosk", page_icon="☕")
st.title("☕ Corner Kiosk")
st.caption("Explore the menu and build a draft order. No payment is taken.")

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid4())
    st.session_state.conversation = []


def api_request(method, path, **kwargs):
    response = requests.request(method, API_URL + path, timeout=180,
        headers={"X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": st.session_state.session_id}, **kwargs)
    if not response.ok:
        try:
            detail = response.json().get("detail", "Backend request failed.")
        except ValueError:
            detail = "Backend request failed."
        raise RuntimeError(str(detail))
    return response.json()


try:
    menu = api_request("GET", "/menu")
except (requests.RequestException, RuntimeError) as exc:
    st.error("Cannot reach the kiosk backend. Start FastAPI on port 8080 first.")
    st.stop()

with st.sidebar:
    st.header("Menu")
    for product in menu:
        st.write(f"**{product['name']}** · ₹{product['price_paise'] / 100:.2f}")
    st.caption("Each browser session has its own draft. This is a classroom session, not a verified login.")
    if st.button("New conversation and draft"):
        try:
            api_request("POST", "/reset")
            st.session_state.conversation = []
            st.rerun()
        except (requests.RequestException, RuntimeError) as exc:
            st.error(str(exc))

for message in st.session_state.conversation:
    with st.chat_message(message["role"]):
        st.write(message["content"])
        if message.get("steps"):
            with st.expander("Tool calls"):
                st.json(message["steps"])

query = st.chat_input("Try: Add one cold coffee and one sandwich")
if query:
    with st.chat_message("user"):
        st.write(query)
    try:
        with st.spinner("Working on your order…"):
            result = api_request("POST", "/chat", json={"prompt": query})
        st.session_state.conversation.extend([
            {"role": "user", "content": query},
            {"role": "assistant", "content": result["answer"], "steps": result["steps"]}])
        st.rerun()
    except (requests.RequestException, RuntimeError) as exc:
        st.error(str(exc))

st.divider()
st.subheader("Your saved draft")
try:
    order = api_request("GET", "/order")
    if not order["items"]:
        st.info("Your draft is empty.")
    for item in order["items"]:
        st.write(f"{item['quantity']} × {item['name']} · ₹{item['quantity'] * item['unit_price_paise'] / 100:.2f}")
    st.write(f"**Total: ₹{order['total_paise'] / 100:.2f}**")
except (requests.RequestException, RuntimeError) as exc:
    st.error("Could not refresh the saved draft: " + str(exc))
