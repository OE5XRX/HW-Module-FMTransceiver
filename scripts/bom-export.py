#!/usr/bin/env python3
"""
bom-export.py – Export a KiCad BOM CSV to InvenTree.

Creates a PCB part and an assembly part in InvenTree, then populates the BOM
with all components from the CSV.  Any parts not yet present in InvenTree are
created automatically (via LCSC / Mouser) before the BOM is assembled.

Required environment variables:
    INVENTREE_API_HOST     – InvenTree server URL
    INVENTREE_API_TOKEN    – API token  (or use USERNAME + PASSWORD instead)
    MOUSER_API_KEY         – Mouser API v2 key
"""

import argparse
import csv
import logging
import sys
from dataclasses import dataclass, field

from inventree.api import InvenTreeAPI
from inventree.company import SupplierPart
from inventree.part import BomItem, Part, PartCategory, PartRelated

from part_importer import ensure_parts_exist

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# Human-readable category names used for PCB and assembly parts.
PCB_CATEGORY_NAME      = "Printed-Circuit Boards"
ASSEMBLY_CATEGORY_NAME = "PCBA"
STENCIL_CATEGORY_NAME  = "SMT Stencil"


def resolve_category(api: InvenTreeAPI, name: str) -> PartCategory:
    """Return the PartCategory with the given name, or abort if not found."""
    matches = [c for c in PartCategory.list(api) if c.name == name]
    if not matches:
        log.error("InvenTree category %r not found. Create it first.", name)
        sys.exit(1)
    return matches[0]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class BomEntry:
    """One row of the KiCad BOM CSV."""
    reference: str
    qty: int
    kicad_part: str
    kicad_value: str
    kicad_footprint: str
    lcsc: list[str] = field(default_factory=list)
    mouser: list[str] = field(default_factory=list)
    inventree_part: list[Part] = field(default_factory=list)


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------

def load_bom(csv_path: str) -> list[BomEntry]:
    """Parse the KiCad BOM CSV and return a list of BomEntry objects."""
    entries: list[BomEntry] = []
    with open(csv_path, newline="") as fh:
        for row in csv.DictReader(fh):
            entries.append(BomEntry(
                reference=row["References"],
                qty=int(row["Quantity Per PCB"]),
                kicad_part=row["Part"].strip(),
                kicad_value=row["Value"].strip(),
                kicad_footprint=row["Footprint"].strip(),
                lcsc=row["LCSC"].split(",") if row["LCSC"].strip() else [],
                mouser=row["MOUSER"].split(",") if row["MOUSER"].strip() else [],
            ))
    log.info("Loaded %d BOM entries from %s", len(entries), csv_path)
    return entries


# ---------------------------------------------------------------------------
# Part matching
# ---------------------------------------------------------------------------

def match_supplier_parts(api: InvenTreeAPI, entries: list[BomEntry]) -> None:
    """
    Match each BomEntry to its InvenTree Part via SupplierPart SKU lookup.
    Populates entry.inventree_part for every entry that has a supplier SKU.
    """
    all_supplier_parts = SupplierPart.list(api)
    sku_to_part: dict[str, Part] = {
        sp.SKU: Part(api, pk=sp.part) for sp in all_supplier_parts
    }

    for entry in entries:
        if entry.inventree_part:
            continue  # already resolved by ensure_parts_exist
        for sku in entry.lcsc + entry.mouser:
            if part := sku_to_part.get(sku):
                entry.inventree_part.append(part)
                break

    missing = [e for e in entries if not e.inventree_part and (e.lcsc or e.mouser)]
    if missing:
        for entry in missing:
            log.error("No InvenTree part found for %s (LCSC=%s, Mouser=%s)",
                      entry.reference, entry.lcsc, entry.mouser)
        sys.exit(1)


# ---------------------------------------------------------------------------
# PCB + assembly creation
# ---------------------------------------------------------------------------

def create_pcb_part(api: InvenTreeAPI, category: PartCategory, name: str, version: str, image: str) -> Part:
    part = Part.create(api, {
        "category": category.pk,
        "name": f"{name} PCB",
        "revision": version,
        "component": True,
    })
    assert part.uploadImage(image) is not None, f"Image upload failed: {image}"
    log.info("Created PCB part '%s PCB' (pk=%s)", name, part.pk)
    return part


def create_assembly_part(api: InvenTreeAPI, category: PartCategory, name: str, version: str, image: str) -> Part:
    part = Part.create(api, {
        "category": category.pk,
        "name": f"{name} Module",
        "revision": version,
        "component": True,
        "assembly": True,
        "trackable": True,
    })
    assert part.uploadImage(image) is not None, f"Image upload failed: {image}"
    log.info("Created assembly part '%s Module' (pk=%s)", name, part.pk)
    return part


def create_stencil_part(api: InvenTreeAPI, category: PartCategory, name: str, version: str, image: str | None = None) -> Part:
    part = Part.create(api, {
        "category": category.pk,
        "name": f"{name} SMT Stencil",
        "revision": version,
        "component": True,
    })
    if image:
        assert part.uploadImage(image) is not None, f"Image upload failed: {image}"
    log.info("Created stencil part '%s SMT Stencil' (pk=%s)", name, part.pk)
    return part



def populate_bom(
    api: InvenTreeAPI,
    assembly: Part,
    pcb: Part,
    entries: list[BomEntry],
) -> None:
    """Create BomItems on *assembly*: one for the PCB, one per BomEntry."""
    BomItem.create(api, {
        "part": assembly.pk,
        "sub_part": pcb.pk,
        "reference": "",
        "quantity": 1,
    })

    for entry in entries:
        for inv_part in entry.inventree_part:
            BomItem.create(api, {
                "part": assembly.pk,
                "sub_part": inv_part.pk,
                "reference": entry.reference,
                "quantity": entry.qty,
            })

    log.info("BOM populated with %d unique components", len(entries))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a KiCad BOM CSV to an InvenTree assembly BOM."
    )
    parser.add_argument("--csv_file",        required=True, help="Path to the KiCad BOM CSV")
    parser.add_argument("--name",            required=True, help="Module name (e.g. HW-Module-FMTransceiver)")
    parser.add_argument("--version",         required=True, help="Revision string (e.g. 0.99)")
    parser.add_argument("--pcb_image",       required=True,  help="PCB render image")
    parser.add_argument("--assembly_image",  required=True,  help="Assembly render image")
    parser.add_argument("--stencil_image",   required=False, help="Stencil paste-layer render (optional)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Connection settings are read from environment variables by InvenTreeAPI:
    #   INVENTREE_API_HOST  +  INVENTREE_API_TOKEN
    #   (or INVENTREE_API_USERNAME / INVENTREE_API_PASSWORD)
    api = InvenTreeAPI()

    entries = load_bom(args.csv_file)

    # Create any parts that don't exist in InvenTree yet
    ensure_parts_exist(api, entries)

    # Match every BOM entry to its InvenTree part via supplier SKU
    match_supplier_parts(api, entries)

    pcb_cat      = resolve_category(api, PCB_CATEGORY_NAME)
    assembly_cat = resolve_category(api, ASSEMBLY_CATEGORY_NAME)
    stencil_cat  = resolve_category(api, STENCIL_CATEGORY_NAME)

    pcb      = create_pcb_part(api, pcb_cat, args.name, args.version, args.pcb_image)
    assembly = create_assembly_part(api, assembly_cat, args.name, args.version, args.assembly_image)
    stencil  = create_stencil_part(api, stencil_cat, args.name, args.version, args.stencil_image)

    # Link stencil ↔ PCB as related parts (not BOM – the stencil is a
    # production tool, not a consumed component of the assembly).
    PartRelated.add_related(api, pcb, stencil)
    log.info("Linked stencil to PCB as related part")

    populate_bom(api, assembly, pcb, entries)


if __name__ == "__main__":
    main()
