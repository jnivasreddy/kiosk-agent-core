"""Three tools. Customer identity comes from our application, never the model."""
from typing import Annotated
from langchain_core.tools import tool
from pydantic import BaseModel, Field
import database


class OrderItem(BaseModel):
    product_id: str = Field(description="An exact product_id from the menu")
    quantity: int = Field(ge=1, le=20, strict=True)


def create_tools(customer_id):
    @tool
    def get_menu() -> list:
        """Read the menu with product IDs, prices in paise and availability."""
        return database.MENU

    @tool
    def get_current_order() -> dict:
        """Read this customer's saved draft order."""
        return database.get_order(customer_id)

    @tool
    def set_order(items: Annotated[list[OrderItem], Field(max_length=4)]) -> dict:
        """Replace the COMPLETE draft. Read the current order first and preserve
        items the customer wants to keep. Pass [] to clear the draft.
        This does not place an order or take payment.
        """
        return database.save_order(customer_id, [item.model_dump() for item in items])

    return [get_menu, get_current_order, set_order]
