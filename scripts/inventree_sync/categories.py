"""
categories.py – KiCad → InvenTree category mapping and part-name generation.
"""

import logging
import re
from pathlib import Path
from typing import Optional

import yaml
from inventree.api import InvenTreeAPI
from inventree.part import PartCategory

from .models import PartData

logger = logging.getLogger(__name__)

# Path to the built-in category map shipped with the package.
_DEFAULT_CATEGORIES_FILE = Path(__file__).parent / "default_categories.yaml"

# KiCad symbol names that receive an automatic package-level sub-category.
_PACKAGE_SUBCATEGORY_CAPS = frozenset({"C", "C_Small"})
_PACKAGE_SUBCATEGORY_RESISTORS = frozenset({"R", "R_Small", "R_Network", "RN"})


# ---------------------------------------------------------------------------
# Category map loading
# ---------------------------------------------------------------------------

def load_category_map(path: Optional[str] = None) -> dict[str, tuple[str, ...]]:
    """Load a KiCad symbol → InvenTree category map from a YAML file.

    Each YAML key is a KiCad symbol name; its value must be a list of strings
    that form the InvenTree category hierarchy (top-level → sub-category).

    If *path* is None the built-in ``default_categories.yaml`` is used.

    Example YAML entry::

        R: [Resistors, Surface Mount]
        Crystal: [Crystals and Oscillators, Crystals]

    Raises ``SystemExit`` with a descriptive message when the file cannot be
    read or contains an invalid entry.
    """
    file_path = Path(path) if path else _DEFAULT_CATEGORIES_FILE
    try:
        with open(file_path) as fh:
            raw = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        logger.error("Category map file not found: %s", file_path)
        raise SystemExit(f"ERROR: category map file not found: {file_path}")
    except yaml.YAMLError as exc:
        logger.error("Failed to parse category map %s: %s", file_path, exc)
        raise SystemExit(f"ERROR: failed to parse YAML in {file_path}: {exc}")

    result: dict[str, tuple[str, ...]] = {}
    for key, value in raw.items():
        if not isinstance(value, list) or not all(isinstance(s, str) for s in value):
            raise SystemExit(
                f"ERROR: invalid entry in {file_path}: key '{key}' must map to "
                f"a list of strings, got {type(value).__name__!r}"
            )
        result[str(key)] = tuple(value)
    return result


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


def resolve_part_category(
    api: InvenTreeAPI,
    kicad_part: str,
    part_data: PartData,
    footprint: str,
    category_map: Optional[dict[str, tuple[str, ...]]] = None,
) -> Optional[PartCategory]:
    """Return the InvenTree PartCategory for a part, creating it if necessary.

    *category_map* defaults to the built-in map loaded from
    ``default_categories.yaml`` when not provided.
    """
    if category_map is None:
        category_map = load_category_map()

    path = category_map.get(kicad_part)
    if path:
        pkg = extract_package(footprint) if footprint else ""
        # Ceramic caps and resistors get a package-level sub-category.
        if kicad_part in _PACKAGE_SUBCATEGORY_CAPS and pkg:
            path = path + (pkg,)
        elif kicad_part in _PACKAGE_SUBCATEGORY_RESISTORS and pkg:
            path = path + (pkg,)
        return get_or_create_category(api, path)

    # Symbol not in the map – warn so the user can extend the YAML file
    logger.warning(
        "KiCad symbol %r not found in category map; "
        "add it to your categories YAML to assign a specific category.",
        kicad_part,
    )

    # Supplier-provided category path as a fallback
    if part_data and part_data.category_path:
        logger.debug("Using supplier-provided category for %r: %s", kicad_part, part_data.category_path)
        return get_or_create_category(api, tuple(part_data.category_path))

    logger.debug("Falling back to 'Miscellaneous' for %r", kicad_part)
    return get_or_create_category(api, ("Miscellaneous",))
