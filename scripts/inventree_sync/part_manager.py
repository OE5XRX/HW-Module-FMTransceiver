"""
part_manager.py – High-level logic for ensuring BOM parts exist in InvenTree.

Orchestrates fetching from LCSC/Mouser and creating missing parts.
"""

import logging
import re
from typing import Optional

from inventree.api import InvenTreeAPI

from .categories import generate_part_name, resolve_part_category
from .client import (
    create_part_in_inventree,
    ensure_supplier_parts,
    find_existing_part,
    find_part_by_name,
    get_or_create_supplier,
)
from .fetchers import LCSCFetcher, MouserFetcher
from .models import PartData

logger = logging.getLogger(__name__)


def _strip_mouser_prefix(mouser_sku: str) -> str:
    """
    Strip the numeric distributor prefix from a Mouser SKU to recover the MPN.
    '637-2N7002' → '2N7002', '595-LMR51430XDDCR' → 'LMR51430XDDCR'
    Returns the original string when no prefix is found.
    """
    m = re.match(r"^\d+-(.+)$", mouser_sku)
    return m.group(1) if m else mouser_sku


def _fetch_and_merge(
    lcsc_fetcher: LCSCFetcher,
    mouser_fetcher: MouserFetcher,
    lcsc_sku: str,
    mouser_sku: str,
) -> Optional[PartData]:
    """
    Fetch and merge part data from LCSC and Mouser.

    Strategy:
    1. LCSC by SKU (if available) – best source for parameters.
    2. LCSC by MPN derived from Mouser SKU (if no LCSC SKU).
    3. Mouser (if available) – supplements missing image/price.
    LCSC data takes priority; Mouser fills gaps.
    """
    lcsc_data: Optional[PartData] = None
    mouser_data: Optional[PartData] = None

    if lcsc_sku:
        lcsc_data = lcsc_fetcher.fetch_by_sku(lcsc_sku)
    if lcsc_data is None and mouser_sku:
        mpn = _strip_mouser_prefix(mouser_sku)
        lcsc_data = lcsc_fetcher.fetch_by_mpn(mpn)

    if mouser_sku:
        mouser_data = mouser_fetcher.fetch(mouser_sku)

    if lcsc_data is None and mouser_data is None:
        return None

    # Merge: LCSC is primary, Mouser supplements
    if lcsc_data is None:
        result = mouser_data
    elif mouser_data is None:
        result = lcsc_data
    else:
        result = lcsc_data
        if not result.image_url:
            result.image_url = mouser_data.image_url
        if not result.datasheet_url:
            result.datasheet_url = mouser_data.datasheet_url
        if not result.price_breaks:
            result.price_breaks = mouser_data.price_breaks
            result.currency = mouser_data.currency
        if not result.description:
            result.description = mouser_data.description

    # Stamp both SKUs on the merged result
    result.lcsc_sku = lcsc_sku
    result.mouser_sku = mouser_sku
    return result


def ensure_parts_exist(
    api: InvenTreeAPI,
    parts: list,
    category_map: Optional[dict[str, tuple[str, ...]]] = None,
) -> None:
    """
    For every BomEntry in *parts* that is missing from InvenTree, fetch data
    from LCSC / Mouser and create the part automatically.

    Each item in *parts* must have: reference, qty, lcsc, mouser,
    inventree_part, kicad_part, kicad_value, kicad_footprint attributes.

    *category_map* is a dict mapping KiCad symbol names to InvenTree category
    path tuples.  When None, the built-in ``default_categories.yaml`` is used.
    """
    lcsc_fetcher = LCSCFetcher()
    mouser_fetcher = MouserFetcher()

    lcsc_supplier = get_or_create_supplier(api, name="LCSC")
    mouser_supplier = get_or_create_supplier(api, name="Mouser")

    for entry in parts:
        lcsc_skus: list = getattr(entry, "lcsc", [])
        mouser_skus: list = getattr(entry, "mouser", [])
        kicad_part: str = getattr(entry, "kicad_part", "")
        kicad_value: str = getattr(entry, "kicad_value", "")
        kicad_footprint: str = getattr(entry, "kicad_footprint", "")

        if not lcsc_skus and not mouser_skus:
            logger.debug("Skipping part with no SKUs: %s", entry.reference)
            continue

        if getattr(entry, "inventree_part", []):
            continue

        lcsc_sku = lcsc_skus[0] if lcsc_skus else ""
        mouser_sku = mouser_skus[0] if mouser_skus else ""

        # Check if a matching SupplierPart already exists in InvenTree
        existing = find_existing_part(api, lcsc_sku, mouser_sku)
        if existing:
            entry.inventree_part.append(existing)
            logger.info("Found existing part for %s: pk=%s", entry.reference, existing.pk)
            continue

        # Fetch data from suppliers
        part_data = _fetch_and_merge(lcsc_fetcher, mouser_fetcher, lcsc_sku, mouser_sku)
        if part_data is None:
            logger.warning(
                "No supplier data found for %s (LCSC=%s, Mouser=%s)",
                entry.reference, lcsc_sku, mouser_sku,
            )
            continue

        # Generate name; reuse if an InvenTree part with that name already exists
        name = generate_part_name(kicad_part, kicad_value, kicad_footprint)
        existing_by_name = find_part_by_name(api, name)
        if existing_by_name:
            logger.info(
                "Part '%s' already exists (pk=%s); adding missing supplier parts",
                name, existing_by_name.pk,
            )
            ensure_supplier_parts(api, existing_by_name, part_data, lcsc_supplier, mouser_supplier)
            entry.inventree_part.append(existing_by_name)
            continue

        category = resolve_part_category(api, kicad_part, part_data, kicad_footprint, category_map)
        inv_part = create_part_in_inventree(api, name, part_data, category, lcsc_supplier, mouser_supplier)
        if inv_part:
            entry.inventree_part.append(inv_part)
        else:
            logger.error("Failed to create part for %s", entry.reference)
