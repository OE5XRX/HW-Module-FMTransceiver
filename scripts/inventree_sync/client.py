"""
client.py – Low-level InvenTree API helpers for creating and updating parts,
supplier records, manufacturer records, and price breaks.
"""

import logging
import os
import tempfile
from typing import Optional

import requests

from inventree.api import InvenTreeAPI
from inventree.company import Company, ManufacturerPart, SupplierPart, SupplierPriceBreak
from inventree.part import Part, PartCategory

from .fetchers import _IOS_UA
from .models import PartData

logger = logging.getLogger(__name__)


def get_or_create_supplier(api: InvenTreeAPI, pk: int, name: str) -> Optional[Company]:
    """Return the supplier Company by PK; fall back to name search or creation."""
    try:
        return Company(api, pk=pk)
    except Exception:
        pass
    try:
        companies = Company.list(api, name=name, is_supplier=True)
        if companies:
            return companies[0]
        return Company.create(api, {"name": name, "is_supplier": True, "is_manufacturer": False})
    except Exception as exc:
        logger.error("get_or_create_supplier(%s) failed: %s", name, exc)
        return None


def get_or_create_manufacturer(api: InvenTreeAPI, name: str) -> Optional[Company]:
    """Return (or create) a manufacturer Company by name (case-insensitive)."""
    try:
        companies = Company.list(api, is_manufacturer=True)
        for c in companies:
            if c.name.lower() == name.lower():
                return c
        return Company.create(api, {"name": name, "is_manufacturer": True, "is_supplier": False})
    except Exception as exc:
        logger.error("get_or_create_manufacturer(%s) failed: %s", name, exc)
        return None


def upload_image_from_url(part: Part, url: str) -> None:
    """Download an image from *url* and attach it to *part*."""
    if not url:
        return
    try:
        resp = requests.get(url, timeout=20, headers={
            "User-Agent": _IOS_UA,
            "Referer": "https://www.lcsc.com/",
        })
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("Image download failed (%s): %s", url, exc)
        return

    # Guess extension from Content-Type or URL
    content_type = resp.headers.get("Content-Type", "")
    if "jpeg" in content_type or "jpg" in content_type:
        suffix = ".jpg"
    elif "png" in content_type:
        suffix = ".png"
    elif "webp" in content_type:
        suffix = ".webp"
    else:
        suffix = os.path.splitext(url.split("?")[0])[-1] or ".jpg"

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(resp.content)
            tmp_path = tmp.name
        part.uploadImage(tmp_path)
        logger.info("Uploaded image to part %s", part.pk)
    except Exception as exc:
        logger.warning("Image upload failed for part %s: %s", part.pk, exc)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _add_price_breaks(
    api: InvenTreeAPI,
    supplier_part: SupplierPart,
    price_breaks: dict,
    currency: str,
) -> None:
    """Create price break records on *supplier_part*."""
    for qty, price in sorted(price_breaks.items()):
        try:
            SupplierPriceBreak.create(api, {
                "part": supplier_part.pk,
                "quantity": qty,
                "price": str(price),
                "price_currency": currency,
            })
        except Exception as exc:
            logger.warning("Price break creation failed (qty=%s): %s", qty, exc)


def create_part_in_inventree(
    api: InvenTreeAPI,
    name: str,
    part_data: PartData,
    category: Optional[PartCategory],
    lcsc_supplier: Optional[Company],
    mouser_supplier: Optional[Company],
) -> Optional[Part]:
    """
    Create an InvenTree Part (with manufacturer/supplier parts) from *part_data*.
    Returns the created Part, or None on failure.
    """
    # 1. Create the base part
    part_payload = {
        "name": name,
        "description": part_data.description or name,
        "component": True,
        "purchaseable": True,
        "active": True,
    }
    if category:
        part_payload["category"] = category.pk
    if part_data.datasheet_url:
        part_payload["link"] = part_data.datasheet_url

    try:
        part = Part.create(api, part_payload)
        logger.info("Created part '%s' (pk=%s)", name, part.pk)
    except Exception as exc:
        logger.error("Part creation failed for '%s': %s", name, exc)
        return None

    # 2. Upload image
    if part_data.image_url:
        upload_image_from_url(part, part_data.image_url)

    # 3. Manufacturer part
    if part_data.mpn and part_data.manufacturer:
        manufacturer = get_or_create_manufacturer(api, part_data.manufacturer)
        if manufacturer:
            try:
                ManufacturerPart.create(api, {
                    "part": part.pk,
                    "manufacturer": manufacturer.pk,
                    "MPN": part_data.mpn,
                })
                logger.info("Created ManufacturerPart %s / %s", part_data.manufacturer, part_data.mpn)
            except Exception as exc:
                logger.warning("ManufacturerPart creation failed: %s", exc)

    # 4. LCSC supplier part
    if part_data.lcsc_sku and lcsc_supplier:
        try:
            sp = SupplierPart.create(api, {
                "part": part.pk,
                "supplier": lcsc_supplier.pk,
                "SKU": part_data.lcsc_sku,
                "manufacturer_part": None,
            })
            if part_data.price_breaks:
                _add_price_breaks(api, sp, part_data.price_breaks, part_data.currency)
        except Exception as exc:
            logger.warning("LCSC SupplierPart creation failed (%s): %s", part_data.lcsc_sku, exc)

    # 5. Mouser supplier part
    if part_data.mouser_sku and mouser_supplier:
        try:
            sp = SupplierPart.create(api, {
                "part": part.pk,
                "supplier": mouser_supplier.pk,
                "SKU": part_data.mouser_sku,
            })
            # Use Mouser price breaks only if LCSC had none
            if part_data.price_breaks and not part_data.lcsc_sku:
                _add_price_breaks(api, sp, part_data.price_breaks, part_data.currency)
        except Exception as exc:
            logger.warning("Mouser SupplierPart creation failed (%s): %s", part_data.mouser_sku, exc)

    return part


def find_existing_part(
    api: InvenTreeAPI, lcsc_sku: str, mouser_sku: str
) -> Optional[Part]:
    """Return the InvenTree Part if a SupplierPart with a matching SKU exists."""
    for sku in filter(None, [lcsc_sku, mouser_sku]):
        try:
            sp_list = SupplierPart.list(api, SKU=sku)
            if sp_list:
                return Part(api, pk=sp_list[0].part)
        except Exception as exc:
            logger.debug("SupplierPart lookup failed for SKU=%s: %s", sku, exc)
    return None


def find_part_by_name(api: InvenTreeAPI, name: str) -> Optional[Part]:
    """Return the InvenTree Part with an exact name match, or None."""
    if not name:
        return None
    try:
        results = Part.list(api, search=name)
        for part in results:
            if part.name == name:
                return part
    except Exception as exc:
        logger.debug("Part name lookup failed for '%s': %s", name, exc)
    return None


def ensure_supplier_parts(
    api: InvenTreeAPI,
    part: Part,
    part_data: PartData,
    lcsc_supplier: Optional[Company],
    mouser_supplier: Optional[Company],
) -> None:
    """Add any missing SupplierParts to an already-existing InvenTree Part."""
    try:
        existing_skus = {sp.SKU for sp in SupplierPart.list(api, part=part.pk)}
    except Exception:
        existing_skus = set()

    if part_data.lcsc_sku and lcsc_supplier and part_data.lcsc_sku not in existing_skus:
        try:
            sp = SupplierPart.create(api, {
                "part": part.pk,
                "supplier": lcsc_supplier.pk,
                "SKU": part_data.lcsc_sku,
            })
            if part_data.price_breaks:
                _add_price_breaks(api, sp, part_data.price_breaks, part_data.currency)
        except Exception as exc:
            logger.warning("Could not add LCSC supplier part: %s", exc)

    if part_data.mouser_sku and mouser_supplier and part_data.mouser_sku not in existing_skus:
        try:
            SupplierPart.create(api, {
                "part": part.pk,
                "supplier": mouser_supplier.pk,
                "SKU": part_data.mouser_sku,
            })
        except Exception as exc:
            logger.warning("Could not add Mouser supplier part: %s", exc)
