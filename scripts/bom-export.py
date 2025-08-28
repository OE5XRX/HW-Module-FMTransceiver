#!/bin/env python3

import argparse
import csv
from dataclasses import dataclass, field
from inventree import company
from inventree.api import InvenTreeAPI
from inventree.part import Part, PartCategory, BomItem

parser = argparse.ArgumentParser(prog="KiCad Bom export to InvenTree")
parser.add_argument("--csv_file", required=True)
parser.add_argument("--name", required=True)
parser.add_argument("--version", required=True)
parser.add_argument("--pcb_image", required=True)
parser.add_argument("--assembly_image", required=True)
args = parser.parse_args()


# must define connection settings in environment variables!
# INVENTREE_API_HOST
#    INVENTREE_API_USERNAME
#    INVENTREE_API_PASSWORD
# OR
#    INVENTREE_API_TOKEN
api = InvenTreeAPI()


@dataclass(slots=True)
class BuildPart:
    reference: str
    qty: int
    lcsc: list[str] = field(default_factory=list)
    mouser: list[str] = field(default_factory=list)
    inventree_part: list[Part] = field(default_factory=list)

    def compare_and_fetch(self, supplier_part: company.SupplierPart) -> bool:
        if supplier_part.SKU in self.lcsc or supplier_part.SKU in self.mouser:
            self.inventree_part.append(Part(api, pk=supplier_part.part))
            return True
        return False


parts: list[BuildPart] = []

with open(args.csv_file, newline="") as csvfile:
    reader = csv.DictReader(csvfile)
    for row in reader:
        parts.append(
            BuildPart(
                row["References"],
                row["Quantity Per PCB"],
                row["LCSC"].split(",") if len(row["LCSC"]) > 0 else [],
                row["MOUSER"].split(",") if len(row["MOUSER"]) > 0 else [],
            )
        )

# parts matching
supplier_parts = company.SupplierPart.list(api)

for part in parts:
    for supplier_part in supplier_parts:
        if part.compare_and_fetch(supplier_part):
            break

# check all parts if something is missing
for part in parts:
    if len(part.lcsc) != 0 or len(part.mouser) != 0:
        assert len(part.inventree_part) != 0, f"{part}"

pcb_cat = PartCategory(api, 54)
pcb_part = Part.create(
    api,
    {
        "category": pcb_cat.pk,
        "name": f"{args.name} PCB",
        "revision": args.version,
        "component": True,
    },
)
response = pcb_part.uploadImage(args.pcb_image)
assert response is not None

pcb_cat = PartCategory(api, 2)
assembly_part = Part.create(
    api,
    {
        "category": pcb_cat.pk,
        "name": f"{args.name} Module",
        "revision": args.version,
        "component": True,
        "assembly": True,
        "trackable": True,
    },
)
response = assembly_part.uploadImage(args.assembly_image)
assert response is not None

bom_item = BomItem.create(
    api,
    {
        "part": assembly_part.pk,
        "sub_part": pcb_part.pk,
        "reference": "",
        "quantity": 1,
    },
)

for part in parts:
    for inventree_part in part.inventree_part:
        bom_item = BomItem.create(
            api,
            {
                "part": assembly_part.pk,
                "sub_part": inventree_part.pk,
                "reference": part.reference,
                "quantity": part.qty,
            },
        )
