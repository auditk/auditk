"""Data models for inventory service."""

from dataclasses import dataclass, field


@dataclass
class Item:
    id: int
    name: str
    sku: str
    quantity: int
    price: float
    is_active: bool = True


@dataclass
class Warehouse:
    id: int
    name: str
    location: str
    items: list[Item] = field(default_factory=list)
