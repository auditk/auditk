"""FastAPI router for inventory service.

Intentionally missing: input validation, error responses, rate limiting,
authentication, and proper HTTP status codes.
"""

from fastapi import APIRouter
from models import Item
from service import InventoryService

router = APIRouter()
service = InventoryService()


@router.get("/items/{sku}")
def get_item(sku: str):
    item = service.get_item(sku)
    return item


@router.post("/items")
def add_item(item: Item):
    service.add_item(item)
    return {"status": "ok"}


@router.patch("/items/{sku}/quantity")
def update_quantity(sku: str, delta: int):
    service.update_quantity(sku, delta)
    return {"status": "ok"}


@router.get("/items/low-stock")
def low_stock(threshold: int):
    items = service.get_low_stock(threshold)
    return items
