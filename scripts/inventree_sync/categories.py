"""
categories.py – KiCad → InvenTree category mapping and part-name generation.
"""

import logging
import re
from typing import Optional

from inventree.api import InvenTreeAPI
from inventree.part import PartCategory

from .models import PartData

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# KiCad symbol name → InvenTree category hierarchy
# ---------------------------------------------------------------------------
# Passives (R, C) get package sub-categories added dynamically in resolve_category().
KICAD_CATEGORY_MAP: dict[str, tuple[str, ...]] = {
    # Passives
    "R":                           ("Resistors", "Surface Mount"),
    "R_Small":                     ("Resistors", "Surface Mount"),
    "R_Network":                   ("Resistors", "Surface Mount"),
    "RN":                          ("Resistors", "Surface Mount"),
    "R_Potentiometer":             ("Resistors", "Potentiometers"),
    "R_Thermsistor":               ("Resistors", "NTC"),
    "C":                           ("Capacitors", "Ceramic"),
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
) -> Optional[PartCategory]:
    """Return the InvenTree PartCategory for a part, creating it if necessary."""
    path = KICAD_CATEGORY_MAP.get(kicad_part)
    if path:
        pkg = extract_package(footprint) if footprint else ""
        # Ceramic caps and resistors get a package-level sub-category.
        if kicad_part in ("C", "C_Small") and pkg:
            path = path + (pkg,)
        elif kicad_part in ("R", "R_Small", "R_Network", "RN") and pkg:
            path = path + (pkg,)
        return get_or_create_category(api, path)

    # Supplier-provided category path as a fallback
    if part_data and part_data.category_path:
        return get_or_create_category(api, tuple(part_data.category_path))

    return get_or_create_category(api, ("Miscellaneous",))
