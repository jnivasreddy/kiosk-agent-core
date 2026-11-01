"""Plain Python + SQLite. Amounts are stored in paise, not floating point."""
import os
from contextlib import contextmanager
from pathlib import Path
import sqlite3

DB_PATH = Path(os.getenv("KIOSK_DB_PATH", str(Path(__file__).parent / "kiosk.sqlite3")))
MENU = [
    {"product_id": "cold_coffee", "name": "Cold Coffee", "price_paise": 12000, "available": True},
    {"product_id": "veg_sandwich", "name": "Veg Sandwich", "price_paise": 10000, "available": True},
    {"product_id": "paneer_tikka", "name": "Paneer Tikka", "price_paise": 22000, "available": True},
    {"product_id": "lemonade", "name": "Lemonade", "price_paise": 6000, "available": True},
]


@contextmanager
def connect():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("""CREATE TABLE IF NOT EXISTS order_items (
        customer_id TEXT NOT NULL, product_id TEXT NOT NULL,
        quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 20),
        PRIMARY KEY(customer_id, product_id))""")
    try:
        with db:
            yield db
    finally:
        db.close()


def get_order(customer_id):
    menu = {item["product_id"]: item for item in MENU}
    with connect() as db:
        rows = db.execute("SELECT product_id,quantity FROM order_items WHERE customer_id=? ORDER BY product_id",
                          (customer_id,)).fetchall()
    items = [{"product_id": row["product_id"], "name": menu[row["product_id"]]["name"],
              "quantity": row["quantity"], "unit_price_paise": menu[row["product_id"]]["price_paise"]}
             for row in rows]
    return {"status": "draft", "items": items,
            "total_paise": sum(item["quantity"] * item["unit_price_paise"] for item in items)}


def save_order(customer_id, items):
    menu = {item["product_id"]: item for item in MENU}
    seen = set()
    # Validate everything before replacing any existing order lines.
    for item in items:
        product, quantity = item["product_id"], item["quantity"]
        if product not in menu or not menu[product]["available"]:
            raise ValueError("Product unavailable. Read the menu for valid products.")
        if product in seen or type(quantity) is not int or not 1 <= quantity <= 20:
            raise ValueError("Use each product once, with a quantity from 1 to 20.")
        seen.add(product)
    with connect() as db:
        db.execute("DELETE FROM order_items WHERE customer_id=?", (customer_id,))
        db.executemany("INSERT INTO order_items VALUES (?,?,?)",
                       [(customer_id, item["product_id"], item["quantity"]) for item in items])
    return get_order(customer_id)
