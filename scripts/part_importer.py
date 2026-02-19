#!/usr/bin/env python3
"""
part_importer.py – Fetch part data from LCSC and Mouser, then create missing
parts in InvenTree.  Intended to be called before bom-export.py so that every
SKU referenced in the BOM is already present in InvenTree.
"""

import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Optional

import requests

from inventree.api import InvenTreeAPI
from inventree.company import Company, ManufacturerPart, SupplierPart, SupplierPriceBreak
from inventree.part import Part, PartCategory

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class PartData:
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


# ---------------------------------------------------------------------------
# LCSC fetcher
# ---------------------------------------------------------------------------

_IOS_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Mobile/15E148 Safari/604.1"
)


class LCSCFetcher:
    """Fetches part data from the LCSC wmsc API."""

    _UA = _IOS_UA

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self._UA,
            "Accept-Language": "en-US,en",
        })
        # Initialise session / set currency cookie
        try:
            self.session.get(
                "https://wmsc.lcsc.com/wmsc/home/currency?currencyCode=EUR",
                timeout=10,
            )
        except Exception as exc:
            logger.warning("LCSC currency init failed: %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_by_sku(self, lcsc_sku: str) -> Optional[PartData]:
        """Fetch a single part by its LCSC product code."""
        url = f"https://wmsc.lcsc.com/ftps/wm/product/detail?productCode={lcsc_sku}"
        try:
            resp = self.session.get(url, timeout=15)
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:
            logger.error("LCSC fetch_by_sku(%s) failed: %s", lcsc_sku, exc)
            return None

        result = body.get("result")
        if not result:
            logger.warning("LCSC fetch_by_sku(%s): empty result", lcsc_sku)
            return None
        return self._parse(result, lcsc_sku=lcsc_sku)

    def fetch_by_mpn(self, mpn: str) -> Optional[PartData]:
        """Search LCSC by MPN; prefer exact match.

        The search endpoint returns minimal product data (no paramVOList), so
        after identifying the right product code we always call fetch_by_sku to
        get the full detail (parameters, images, price breaks, …).
        """
        url = "https://wmsc.lcsc.com/ftps/wm/search/v2/global"
        try:
            resp = self.session.post(url, json={"keyword": mpn}, timeout=15)
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:
            logger.error("LCSC fetch_by_mpn(%s) failed: %s", mpn, exc)
            return None

        result = body.get("result", {})
        if not result:
            return None

        # Direct match hint from the API
        tip = result.get("tipProductDetailUrlVO")
        if tip:
            code = tip.get("productCode")
            if code:
                return self.fetch_by_sku(code)

        # Walk the search result list
        product_list = (
            result.get("productSearchResultVO", {}).get("productList") or []
        )
        # Prefer exact MPN match, fall back to first result
        best_code = None
        for product in product_list:
            if product.get("productModel", "").upper() == mpn.upper():
                best_code = product.get("productCode")
                break
        if best_code is None and product_list:
            best_code = product_list[0].get("productCode")

        if best_code:
            return self.fetch_by_sku(best_code)

        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _fix_datasheet(url: str) -> str:
        """Rewrite datasheet CDN URLs to the wmsc mirror."""
        if not url:
            return url
        return url.replace(
            "//datasheet.lcsc.com/",
            "//wmsc.lcsc.com/wmsc/upload/file/pdf/v2/",
        )

    def _parse(self, product: dict, lcsc_sku: str = "") -> PartData:
        """Convert a raw LCSC product dict into a PartData."""
        # Image: prefer big image, fall back to first in list
        image_url = product.get("productImageUrlBig", "")
        if not image_url:
            images = product.get("productImages") or []
            if images:
                image_url = images[0]

        # Datasheet
        datasheet = self._fix_datasheet(product.get("pdfUrl", ""))

        # Parameters
        params = {}
        for p in product.get("paramVOList") or []:
            name = p.get("paramNameEn", "").strip()
            value = p.get("paramValueEn", "").strip()
            if name and value:
                params[name] = value

        # Price breaks  {ladder_qty: unit_price_eur}
        price_breaks = {}
        for pb in product.get("productPriceList") or []:
            try:
                qty = int(pb["ladder"])
                price = float(pb["currencyPrice"])
                price_breaks[qty] = price
            except (KeyError, ValueError, TypeError):
                pass

        sku = lcsc_sku or product.get("productCode", "")

        return PartData(
            mpn=product.get("productModel", ""),
            manufacturer=product.get("brandNameEn", ""),
            description=product.get("productDescEn", ""),
            image_url=image_url,
            datasheet_url=datasheet,
            lcsc_sku=sku,
            package=product.get("encapStandard", ""),
            parameters=params,
            price_breaks=price_breaks,
            currency="EUR",
        )


# ---------------------------------------------------------------------------
# Mouser fetcher
# ---------------------------------------------------------------------------

class MouserFetcher:
    """Fetches part data from the Mouser API v2.

    Requires the ``MOUSER_API_KEY`` environment variable to be set.
    """

    _URL = "https://api.mouser.com/api/v2/search/partnumber"

    def __init__(self):
        self.api_key = os.environ.get("MOUSER_API_KEY")
        if not self.api_key:
            raise EnvironmentError(
                "MOUSER_API_KEY environment variable is not set. "
                "Export it before running this script."
            )

    def fetch(self, mouser_sku: str) -> Optional[PartData]:
        """Return PartData for a Mouser SKU, or None on failure."""
        payload = {
            "SearchByPartRequest": {
                "mouserPartNumber": mouser_sku,
                "partSearchOptions": "Exact",
            }
        }
        try:
            resp = requests.post(
                self._URL,
                params={"apiKey": self.api_key},
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:
            logger.error("Mouser fetch(%s) failed: %s", mouser_sku, exc)
            return None

        parts = (
            body.get("SearchResults", {}).get("Parts") or []
        )
        if not parts:
            logger.warning("Mouser fetch(%s): no results", mouser_sku)
            return None

        p = parts[0]

        # Strip HTML tags from description
        description = re.sub(r"<[^>]+>", "", p.get("Description", ""))

        # Category
        category_path = []
        cat = p.get("Category", "").strip()
        if cat:
            category_path = [cat]

        # Price breaks
        price_breaks = {}
        currency = "EUR"
        for pb in p.get("PriceBreaks") or []:
            try:
                qty = int(pb["Quantity"])
                price = self._parse_price(pb.get("Price", "0"))
                price_breaks[qty] = price
                if pb.get("Currency"):
                    currency = pb["Currency"]
            except (KeyError, ValueError, TypeError):
                pass

        return PartData(
            mpn=p.get("ManufacturerPartNumber", ""),
            manufacturer=p.get("Manufacturer", ""),
            description=description,
            image_url=p.get("ImagePath", ""),
            datasheet_url=p.get("DataSheetUrl", ""),
            mouser_sku=mouser_sku,
            category_path=category_path,
            price_breaks=price_breaks,
            currency=currency,
        )

    @staticmethod
    def _parse_price(price_str: str) -> float:
        """
        Parse a Mouser price string into a float.
        Handles formats like "€ 7,07", "0.1234", "$ 1.23".
        """
        # Strip currency symbols and whitespace
        cleaned = re.sub(r"[^\d,.]", "", price_str.strip())
        if not cleaned:
            return 0.0
        # If both comma and dot present, the one that appears last is the decimal separator
        last_comma = cleaned.rfind(",")
        last_dot = cleaned.rfind(".")
        if last_comma > last_dot:
            # European format: 7,07 or 1.234,56
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            # US format: remove commas used as thousands separator
            cleaned = cleaned.replace(",", "")
        return float(cleaned)


# ---------------------------------------------------------------------------
# Naming helpers
# ---------------------------------------------------------------------------

def extract_package(footprint: str) -> str:
    """
    Extract a short package code from a KiCad footprint string.
    'C_0805_2012Metric' → '0805', 'SOT-23' → 'SOT-23', 'SOIC-8_3.9x...' → 'SOIC-8'
    """
    m = re.match(r"(?:C|R|L)_(\w+?)_", footprint)
    if m:
        return m.group(1)
    return footprint.split("_")[0]


def generate_part_name(kicad_part: str, kicad_value: str, footprint: str) -> str:
    """
    Generate a human-readable InvenTree part name from KiCad fields.

    Examples:
      R, '10k', 'R_0805_2012Metric'          → 'R 10k 0805'
      C, '100nF', 'C_0805_2012Metric'         → 'C 100nF 0805'
      C_Polarized, '100u / 25V', ...           → 'CP 100u/25V'
      Crystal, '8MHz / 20pF', ...              → 'XTAL 8MHz/20pF'
      STM32U575CITx, 'STM32U575CITx', ...     → 'STM32U575CITx'
    """
    # Normalise value: collapse spaces around '/' and consecutive spaces
    val = re.sub(r"\s*/\s*", "/", kicad_value.strip())
    val = re.sub(r"\s+", " ", val).strip()

    if kicad_part == "R":
        return f"R {val} {extract_package(footprint)}"
    elif kicad_part == "C":
        return f"C {val} {extract_package(footprint)}"
    elif kicad_part == "C_Polarized":
        return f"CP {val}"
    elif kicad_part in ("L", "L_Iron"):
        return f"L {val}"
    elif kicad_part == "Crystal":
        return f"XTAL {val}"
    else:
        return val


# ---------------------------------------------------------------------------
# Category mapping
# ---------------------------------------------------------------------------

# KiCad symbol name → InvenTree category hierarchy
# Matches the actual category tree in InvenTree.
# Passives (R, C) get package sub-categories added dynamically in resolve_category().
KICAD_CATEGORY_MAP: dict[str, tuple[str, ...]] = {
    # Passives
    "R":                           ("Resistors", "Surface Mount"),      # + package sub-cat
    "R_Small":                     ("Resistors", "Surface Mount"),
    "R_Network":                   ("Resistors", "Surface Mount"),
    "RN":                          ("Resistors", "Surface Mount"),
    "R_Potentiometer":             ("Resistors", "Potentiometers"),
    "R_Thermsistor":               ("Resistors", "NTC"),
    "C":                           ("Capacitors", "Ceramic"),           # + package sub-cat
    "C_Small":                     ("Capacitors", "Ceramic"),
    "C_Polarized":                 ("Capacitors", "Aluminium"),
    "C_Polarized_Small":           ("Capacitors", "Aluminium"),
    "CP":                          ("Capacitors", "Aluminium"),
    "C_Tantalum":                  ("Capacitors", "Tantalum"),
    "C_Polymer":                   ("Capacitors", "Polymer"),
    "C_SuperCapacitor":            ("Capacitors", "Super Capacitors"),
    "L":                           ("Inductors", "Power"),
    "L_Iron":                      ("Inductors", "Power"),
    "L_Small":                     ("Inductors", "Power"),
    "Ferrite_Bead":                ("Inductors", "Ferrite Bead"),
    "Ferrite_Bead_Small":          ("Inductors", "Ferrite Bead"),
    # Semiconductors – Diodes
    "D":                           ("Diodes", "Standard"),
    "D_Schottky":                  ("Diodes", "Schottky"),
    "D_Zener":                     ("Diodes", "Zener"),
    "D_TVS":                       ("Circuit Protections", "TVS"),
    "LED":                         ("Diodes", "LED"),
    "LED_RGB":                     ("Diodes", "LED"),
    # Specific parts present in this BOM
    "BAT43W-V":                    ("Diodes", "Schottky"),
    "USBLC6-2SC6":                 ("Circuit Protections", "TVS"),
    # Semiconductors – Transistors
    "Q_NPN_BCE":                   ("Transistors", "NPN"),
    "Q_PNP_BCE":                   ("Transistors", "PNP"),
    "Q_NMOS_GSD":                  ("Transistors", "N-Channel FET"),
    "Q_PMOS_GSD":                  ("Transistors", "P-Channel FET"),
    "Q_NMOS_GDS":                  ("Transistors", "N-Channel FET"),
    "Q_Load_Switch_P":             ("Transistors", "Load Switches"),
    "2N7002":                      ("Transistors", "N-Channel FET"),
    "BC847BS":                     ("Transistors", "NPN"),
    # Crystals & oscillators
    "Crystal":                     ("Crystals and Oscillators", "Crystals"),
    "Crystal_GND24":               ("Crystals and Oscillators", "Crystals"),
    "Oscillator":                  ("Crystals and Oscillators", "Oscillators"),
    # Power management
    "LMR51430":                    ("Power Management", "Buck"),
    "TLV73333PDBV":                ("Power Management", "LDO"),
    "Regulator_Linear":            ("Power Management", "LDO"),
    "Regulator_Switching":         ("Power Management", "Buck"),
    # Integrated circuits
    "STM32U575CITx":               ("Integrated Circuits", "Microcontroller"),
    "CAT24C128":                   ("Integrated Circuits", "Memory"),
    "IC_Generic":                  ("Integrated Circuits",),
    # RF
    "SA818V":                      ("RF", "Chipset"),
    "LFCN-160":                    ("RF", "Filter"),
    "Antenna":                     ("RF", "Antenna"),
    "Antenna_Shield":              ("RF", "Shield"),
    # Connectors
    "Conn_Coaxial":                ("Connectors", "Coaxial"),
    "Conn_01x01":                  ("Connectors", "Header"),
    "Conn_01x02":                  ("Connectors", "Header"),
    "Conn_01x03":                  ("Connectors", "Header"),
    "Conn_01x04":                  ("Connectors", "Header"),
    "Conn_02x10_Row_Letter_First": ("Connectors", "Header"),
    "Conn_ARM_JTAG_SWD_10":        ("Connectors", "Header"),
    "USB_C_Receptacle":            ("Connectors", "Interface"),
    "USB_B_Micro":                 ("Connectors", "Interface"),
    "Conn_FPC":                    ("Connectors", "FPC"),
    "BatteryHolder":               ("Connectors", "Battery"),
    # Mechanicals
    "SW_Push":                     ("Mechanicals", "Switch"),
    "SW_SPDT":                     ("Mechanicals", "Switch"),
    "Mounting_Hole":               ("Mechanicals",),
}

def get_or_create_category(api: InvenTreeAPI, path_tuple: tuple) -> Optional[PartCategory]:
    """
    Walk the category hierarchy, creating any levels that don't yet exist.
    Returns the leaf PartCategory.
    """
    parent = None
    category = None
    for name in path_tuple:
        search_kwargs = {"name": name}
        if parent:
            search_kwargs["parent"] = parent.pk
        try:
            cats = PartCategory.list(api, **search_kwargs)
        except Exception as exc:
            logger.error("Category list failed for '%s': %s", name, exc)
            return None

        if cats:
            category = cats[0]
        else:
            data = {"name": name, "description": name}
            if parent:
                data["parent"] = parent.pk
            try:
                category = PartCategory.create(api, data)
                logger.info("Created category '%s'", name)
            except Exception as exc:
                logger.error("Category create failed for '%s': %s", name, exc)
                return None
        parent = category
    return category


def resolve_category(
    api: InvenTreeAPI,
    kicad_part: str,
    part_data: PartData,
    footprint: str,
) -> Optional[PartCategory]:
    """Return the InvenTree PartCategory for a part, creating it if necessary."""
    path = KICAD_CATEGORY_MAP.get(kicad_part)
    if path:
        pkg = extract_package(footprint) if footprint else ""
        # Ceramic caps and resistors get a package-level sub-category.
        # get_or_create_category will create it if it doesn't exist yet.
        if kicad_part in ("C", "C_Small") and pkg:
            path = path + (pkg,)
        elif kicad_part in ("R", "R_Small", "R_Network", "RN") and pkg:
            path = path + (pkg,)
        return get_or_create_category(api, path)

    # Supplier-provided category path as a fallback
    if part_data and part_data.category_path:
        return get_or_create_category(api, tuple(part_data.category_path))

    return get_or_create_category(api, ("Miscellaneous",))


# ---------------------------------------------------------------------------
# InvenTree helpers
# ---------------------------------------------------------------------------

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
    manufacturer = None
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
            if part_data.price_breaks and part_data.lcsc_sku:
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
            if part_data.mouser_sku and part_data.price_breaks and not part_data.lcsc_sku:
                _add_price_breaks(api, sp, part_data.price_breaks, part_data.currency)
        except Exception as exc:
            logger.warning("Mouser SupplierPart creation failed (%s): %s", part_data.mouser_sku, exc)

    return part


# ---------------------------------------------------------------------------
# Fetch & merge
# ---------------------------------------------------------------------------

def _strip_mouser_prefix(mouser_sku: str) -> str:
    """
    Strip the numeric distributor prefix from a Mouser SKU to recover the MPN.
    '637-2N7002' → '2N7002', '595-LMR51430XDDCR' → 'LMR51430XDDCR'
    Returns the original string when no prefix is found.
    """
    m = re.match(r"^\d+-(.+)$", mouser_sku)
    return m.group(1) if m else mouser_sku


def _fetch_part_data(
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

    # Step 1 / 2: try LCSC
    if lcsc_sku:
        lcsc_data = lcsc_fetcher.fetch_by_sku(lcsc_sku)
    if lcsc_data is None and mouser_sku:
        mpn = _strip_mouser_prefix(mouser_sku)
        lcsc_data = lcsc_fetcher.fetch_by_mpn(mpn)

    # Step 3: try Mouser
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


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def ensure_parts_exist(api: InvenTreeAPI, parts: list) -> None:
    """
    For every BuildPart in *parts* that is missing from InvenTree, fetch data
    from LCSC / Mouser and create the part automatically.

    *parts* is a list of BuildPart objects as defined in bom-export.py.
    Each BuildPart must have: reference, qty, lcsc, mouser, inventree_part,
    kicad_part, kicad_value, kicad_footprint attributes.
    """
    lcsc_fetcher = LCSCFetcher()
    mouser_fetcher = MouserFetcher()

    # Resolve supplier companies (pk=3 for LCSC, pk=2 for Mouser)
    lcsc_supplier = get_or_create_supplier(api, pk=3, name="LCSC")
    mouser_supplier = get_or_create_supplier(api, pk=2, name="Mouser")

    for build_part in parts:
        lcsc_skus: list = getattr(build_part, "lcsc", [])
        mouser_skus: list = getattr(build_part, "mouser", [])
        kicad_part: str = getattr(build_part, "kicad_part", "")
        kicad_value: str = getattr(build_part, "kicad_value", "")
        kicad_footprint: str = getattr(build_part, "kicad_footprint", "")

        # Nothing to do if there are no supplier SKUs
        if not lcsc_skus and not mouser_skus:
            logger.debug("Skipping part with no SKUs: %s", build_part.reference)
            continue

        # Already resolved
        if getattr(build_part, "inventree_part", []):
            continue

        lcsc_sku = lcsc_skus[0] if lcsc_skus else ""
        mouser_sku = mouser_skus[0] if mouser_skus else ""

        # Check if a matching SupplierPart already exists in InvenTree
        existing_part = _find_existing_part(api, lcsc_sku, mouser_sku)
        if existing_part:
            build_part.inventree_part.append(existing_part)
            logger.info(
                "Found existing part for %s: pk=%s", build_part.reference, existing_part.pk
            )
            continue

        # Fetch data from suppliers
        part_data = _fetch_part_data(lcsc_fetcher, mouser_fetcher, lcsc_sku, mouser_sku)
        if part_data is None:
            logger.warning(
                "No supplier data found for %s (LCSC=%s, Mouser=%s)",
                build_part.reference, lcsc_sku, mouser_sku,
            )
            continue

        # Generate name and check for an existing InvenTree part with that name
        name = generate_part_name(kicad_part, kicad_value, kicad_footprint)
        existing_by_name = _find_part_by_name(api, name)
        if existing_by_name:
            logger.info("Part '%s' already exists (pk=%s); adding missing supplier parts", name, existing_by_name.pk)
            _ensure_supplier_parts(
                api, existing_by_name, part_data,
                lcsc_supplier, mouser_supplier,
            )
            build_part.inventree_part.append(existing_by_name)
            continue

        # Determine category
        category = resolve_category(api, kicad_part, part_data, kicad_footprint)

        # Create the part
        inv_part = create_part_in_inventree(
            api, name, part_data, category, lcsc_supplier, mouser_supplier,
        )
        if inv_part:
            build_part.inventree_part.append(inv_part)
        else:
            logger.error("Failed to create part for %s", build_part.reference)


# ---------------------------------------------------------------------------
# Internal lookup helpers
# ---------------------------------------------------------------------------

def _find_existing_part(
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


def _find_part_by_name(api: InvenTreeAPI, name: str) -> Optional[Part]:
    """Return the InvenTree Part with an exact name match, or None."""
    if not name:
        return None
    try:
        # Part.list(name=...) does not filter server-side; filter manually.
        results = Part.list(api, search=name)
        for part in results:
            if part.name == name:
                return part
    except Exception as exc:
        logger.debug("Part name lookup failed for '%s': %s", name, exc)
    return None


def _ensure_supplier_parts(
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
