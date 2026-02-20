"""
models.py – Shared data-model dataclasses used by the InvenTree sync scripts.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from inventree.part import Part


@dataclass
class PartData:
    """Raw supplier data fetched from LCSC / Mouser before creating an InvenTree Part."""

    mpn: str = ""
    manufacturer: str = ""
    description: str = ""
    image_url: str = ""
    datasheet_url: str = ""
    supplier_link: str = ""
    lcsc_sku: str = ""
    mouser_sku: str = ""
    category_path: list = field(default_factory=list)
    parameters: dict = field(default_factory=dict)
    price_breaks: dict = field(default_factory=dict)  # {qty: unit_price}
    currency: str = "EUR"
    package: str = ""


@dataclass(slots=True)
class BomEntry:
    """One row of the KiCad BOM CSV."""

    reference: str
    qty: int
    kicad_part: str
    kicad_value: str
    kicad_footprint: str
    lcsc: list = field(default_factory=list)
    mouser: list = field(default_factory=list)
    inventree_part: list = field(default_factory=list)  # list[Part]
