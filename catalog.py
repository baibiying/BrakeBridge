import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path

CATALOG_PATH = Path(__file__).parent / "data" / "catalog.csv"


def find_candidates(fields):
    if fields.get("code", "").casefold().replace(" ", "") not in {"t850", "新t850", "newt850"}:
        return [], ""
    with CATALOG_PATH.open(encoding="utf-8-sig", newline="") as source:
        records = list(csv.DictReader(source))
    constraints = {"position": fields.get("position"), "width_mm": fields.get("width_mm"),
                   "holes": fields.get("holes")}
    candidates = []
    for row in records:
        if fields["code"].casefold().replace(" ", "") != "t850" and row["series"] != "新T850":
            continue
        if constraints["position"] and row["position"] != constraints["position"]:
            continue
        if any(number_differs(row[column], constraints[column]) for column in ("width_mm", "holes")):
            continue
        candidates.append(row)
    if not candidates:
        return [], "Could you share the old lining number, axle details or a photo with dimensions?"
    if len({row["position"] for row in candidates}) > 1:
        return candidates, "Is this for the front or rear axle?"
    if len(candidates) > 1 and len({row["width_mm"] for row in candidates}) > 1:
        widths = " or ".join(sorted({row["width_mm"] for row in candidates}, key=int))
        return candidates, f"What is the width of the old lining in millimeters ({widths} mm)?"
    return candidates, "Could you confirm the old lining dimensions and the axle model before we quote?"


def number_differs(source, provided):
    if provided in (None, ""):
        return False
    try:
        return Decimal(source) != Decimal(str(provided))
    except InvalidOperation:
        return True
