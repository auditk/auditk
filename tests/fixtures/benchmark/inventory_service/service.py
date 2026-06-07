"""Business logic for inventory service.

Intentionally imperfect: missing error handling, inconsistent type hints,
no logging, no docstrings on some methods.
"""

from models import Item


class InventoryService:
    def __init__(self):
        self._items = {}

    def get_item(self, sku) -> Item | None:
        return self._items.get(sku)

    def add_item(self, item: Item) -> None:
        self._items[item.sku] = item

    def update_quantity(self, sku, delta) -> bool:
        item = self._items.get(sku)
        if item is None:
            return False
        item.quantity += delta
        return True

    def deactivate_item(self, sku) -> bool:
        item = self._items.get(sku)
        if item is None:
            return False
        item.is_active = False
        return True

    def get_low_stock(self, threshold) -> list[Item]:
        return [item for item in self._items.values() if item.quantity < threshold]

    def get_all_items(self) -> list[Item]:
        return list(self._items.values())
