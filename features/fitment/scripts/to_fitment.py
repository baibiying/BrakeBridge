#!/usr/bin/env python3
"""C-01: 试点_适配表_153条.csv → data/fitment/fitment.jsonl"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CSV_PATH = ROOT / "试点_适配表_153条.csv"
OUT_PATH = ROOT / "data" / "fitment" / "fitment.jsonl"

VARIANT_MAP = {
    "\u2eec": "风",  # ⻛
    "\u2ef0": "龙",  # ⻰
    "\u2ec1": "虎",  # ⻁
    "\u2ee2": "马",  # ⻢
    "\u2edb": "车",  # ⻋
    "\u2f2f": "田",  # compatibility ideographs sometimes appear
}

# Kangxi / CJK radicals used in the CSV dump
EXTRA_VARIANTS = str.maketrans(
    {
        "⻛": "风",
        "⻰": "龙",
        "⻁": "虎",
        "⻢": "马",
        "⻋": "车",
        "⻘": "青",
        "⽇": "日",
        "⼀": "一",
        "⼩": "小",
        "⻁": "虎",
        "⾦": "金",
        "⽥": "田",
    }
)

SIZE_RE = re.compile(
    r"^\s*([\d.]+)\s*[×xX]\s*([\d.]+)\s*[×xX]\s*([\d.]+)(?:\s*/\s*([\d.]+))?\s*$"
)
ABS_RE = re.compile(r"[\(（]\s*ABS\s*[\)）]", re.I)
# trailing position / variant / 改型 — apply repeatedly from the end
TAIL_RE = re.compile(r"(前|后|改型|[AB])$", re.I)


def fix_variants(s: str) -> str:
    s = s.translate(EXTRA_VARIANTS)
    for a, b in VARIANT_MAP.items():
        s = s.replace(a, b)
    # strip leftover control / private-use garbage from OCR
    s = re.sub(r"[\x00-\x1f\u0000-\u001f]+", "", s)
    return s


def to_half_width(s: str) -> str:
    out = []
    for ch in s:
        code = ord(ch)
        if code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


def parse_size(size_raw: str | None):
    if not size_raw or not str(size_raw).strip():
        return None, None, None, None
    m = SIZE_RE.match(str(size_raw).strip())
    if not m:
        return None, None, None, None
    l, w, t1, t2 = m.groups()
    return (
        float(l),
        float(w),
        float(t1),
        float(t2) if t2 else None,
    )


def split_vehicle(vehicle_raw: str):
    """Extract vehicle_norm, position, variant from raw name."""
    raw = fix_variants(vehicle_raw or "")
    raw = to_half_width(raw)
    raw = raw.replace(" ", "").replace("\u3000", "")
    work = ABS_RE.sub("", raw).strip()

    position = None
    variant = None

    # peel trailing markers: 前A / 后B / 前 / 后 / A / B / 改型
    changed = True
    while changed and work:
        changed = False
        # 前A / 后B as pair
        m = re.search(r"(前|后)([AB])$", work, re.I)
        if m:
            position = m.group(1)
            variant = m.group(2).upper()
            work = work[: m.start()]
            changed = True
            continue
        m = re.search(r"(前|后)$", work)
        if m:
            position = m.group(1)
            work = work[: m.start()]
            changed = True
            continue
        m = re.search(r"([AB])$", work, re.I)
        if m and len(work) > 1:
            # avoid eating lone letter brands; only if preceded by digit or CJK
            if re.search(r"[\u4e00-\u9fff\d]$", work[:-1]):
                variant = m.group(1).upper()
                work = work[: m.start()]
                changed = True
                continue
        if work.endswith("改型"):
            work = work[:-2]
            changed = True
            continue

    vehicle_norm = work.strip() or None
    return vehicle_norm, position, variant


def brand_and_src(file_key: str):
    key = (file_key or "").strip().lower()
    if key == "kooying":
        return "KOOYING", "kooying catalog.pdf", "KL"
    if key == "guanlong":
        return "GUANLONG", "冠隆产品册.pdf", "GL"
    return key.upper() or "UNK", f"{key}.pdf", "XX"


def main() -> int:
    if not CSV_PATH.exists():
        print(f"missing {CSV_PATH}", file=sys.stderr)
        return 1

    # BOM-safe
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    counters: dict[tuple, int] = defaultdict(int)
    records = []

    for row in rows:
        file_key = (row.get("file") or "").strip()
        page = int(row["page"])
        col = int(row["col"])
        vehicle_raw = (row.get("vehicle") or "").strip()
        # drop OCR control chars in vehicle
        vehicle_raw = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", vehicle_raw)
        size_raw = (row.get("size") or "").strip() or None
        if size_raw:
            size_raw = size_raw.replace("x", "×").replace("X", "×")
        oe_raw = (row.get("oe") or "").strip()
        oe = [x.strip() for x in oe_raw.split(";") if x.strip()] if oe_raw else []

        brand, src, prefix = brand_and_src(file_key)
        counters[(prefix, page, col)] += 1
        r_idx = counters[(prefix, page, col)]
        rid = f"{prefix}-p{page}-c{col}-r{r_idx}"
        cell = f"c{col}r{r_idx}"

        vehicle_norm, position, variant = split_vehicle(vehicle_raw) if vehicle_raw else (None, None, None)
        l, w, t1, t2 = parse_size(size_raw)

        if size_raw and vehicle_raw:
            status = "ok"
            confidence = 0.95
        elif vehicle_raw and not size_raw:
            status = "name_only"
            confidence = 0.5
        elif size_raw and not vehicle_raw:
            status = "待核"
            confidence = 0.5
            vehicle_raw = ""
        else:
            status = "待核"
            confidence = 0.5

        records.append(
            {
                "id": rid,
                "brand": brand,
                "src": src,
                "page": page,
                "cell": cell,
                "vehicle_raw": vehicle_raw,
                "vehicle_norm": vehicle_norm,
                "position": position,
                "variant": variant,
                "size_raw": size_raw,
                "l": l,
                "w": w,
                "t1": t1,
                "t2": t2,
                "holes": None,
                "pitch": None,
                "pieces": None,
                "oe": oe,
                "status": status,
                "confidence": confidence,
            }
        )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    ids = [r["id"] for r in records]
    print(f"wrote {len(records)} → {OUT_PATH}")
    print(f"unique ids: {len(set(ids)) == len(ids)}")
    print("sample:")
    for rec in records[:5]:
        print(
            rec["id"],
            rec["vehicle_raw"],
            "→",
            rec["vehicle_norm"],
            rec["position"],
            rec["variant"],
            rec["size_raw"],
            rec["l"],
            rec["w"],
            rec["t1"],
            rec["t2"],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
