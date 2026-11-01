"""Run: python -m streamlit run app.py"""
import logging
from uuid import uuid4
import streamlit as st
from agent import respond
import database

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
st.set_page_config(page_title="Corner Kiosk", page_icon="☕")
st.title("☕ Corner Kiosk")
st.caption("Explore the menu and build a draft order. No payment is taken.")

if "customer_id" not in st.session_state:
    st.session_state.customer_id = str(uuid4())
    st.session_state.history = []
    st.session_state.conversation = []

with st.sidebar:
    st.header("Menu")
    for product in database.MENU:
        st.write(f"**{product['name']}** · ₹{product['price_paise'] / 100:.2f}")
    st.caption("Each browser session has its own draft. Reloading may start a new session.")
    if st.button("New conversation and draft"):
        database.save_order(st.session_state.customer_id, [])
        st.session_state.customer_id = str(uuid4())
        st.session_state.history = []
        st.session_state.conversation = []
        st.rerun()

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
            result = respond(query, st.session_state.customer_id, st.session_state.history)
        st.session_state.history = result["history"]
        st.session_state.conversation.extend([
            {"role": "user", "content": query},
            {"role": "assistant", "content": result["answer"], "steps": result["steps"]}])
        st.rerun()
    except Exception:
        logging.exception("Chat request failed")
        st.error("Could not complete that request. Your draft is shown below. Check the terminal for details.")

order = database.get_order(st.session_state.customer_id)
st.divider()
st.subheader("Your saved draft")
if not order["items"]:
    st.info("Your draft is empty.")
for item in order["items"]:
    st.write(f"{item['quantity']} × {item['name']} · ₹{item['quantity'] * item['unit_price_paise'] / 100:.2f}")
st.write(f"**Total: ₹{order['total_paise'] / 100:.2f}**")
