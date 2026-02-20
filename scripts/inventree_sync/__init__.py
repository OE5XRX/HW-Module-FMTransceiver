"""
inventree_sync – Python package for syncing KiCad BOM data to InvenTree.

Public API
----------
ensure_parts_exist   – create missing InvenTree parts from a BOM
load_category_map    – load a KiCad→InvenTree category map from a YAML file
BomEntry             – dataclass for one KiCad BOM row
PartData             – dataclass for raw supplier part data
"""

from .categories import load_category_map
from .models import BomEntry, PartData
from .part_manager import ensure_parts_exist

__all__ = [
    "BomEntry",
    "load_category_map",
    "PartData",
    "ensure_parts_exist",
]
