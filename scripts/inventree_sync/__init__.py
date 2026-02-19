"""
inventree_sync – Python package for syncing KiCad BOM data to InvenTree.

Public API
----------
ensure_parts_exist   – create missing InvenTree parts from a BOM
BomEntry             – dataclass for one KiCad BOM row
PartData             – dataclass for raw supplier part data
"""

from .models import BomEntry, PartData
from .part_manager import ensure_parts_exist

__all__ = [
    "BomEntry",
    "PartData",
    "ensure_parts_exist",
]
