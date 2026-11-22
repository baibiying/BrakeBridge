"""Match products and prepare a reviewable quote from the local fitment catalogue."""

from __future__ import annotations

import re

from flask import Blueprint, request

import lead_inquiries as leads
from inquiries import WIDTH
from lead_routes import record
from workspace import error

from .match import _norm_code, load_parts_by_code, load_records, match
from .quote import build_quote
from .wizard import decide_next

fitment_bp = Blueprint("fitment", __name__, url_prefix="/api/fitment")

TEXT_FIELDS = {"q": 96, "axle": 96, "code": 96, "year": 12, "tonnage": 24, "brake_type": 8, "position": 4}
CATALOG_CODE = re.compile(r"(?<![A-Z0-9-])[A-Z0-9][A-Z0-9-]{2,45}(?![A-Z0-9-])", re.ASCII | re.I)
HOLES = re.compile(r"(?<!\d)(\d{1,3})\s*(?:holes?\b|孔)", re.I)


def number(raw, label, *, maximum, integer=False):
    if raw in ("", None):
        return None
    if isinstance(raw, bool) or (integer and isinstance(raw, float)):
        raise ValueError(f"请填写有效的{label}。")
    try:
        parsed = int(raw) if integer else float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"请填写有效的{label}。") from exc
    if not 0 < parsed <= maximum:
        raise ValueError(f"请填写有效的{label}。")
    return parsed


def specifications(values):
    params = {key: leads.plain(values.get(key), limit) for key, limit in TEXT_FIELDS.items()}
    if params["position"] not in ("", "前", "后"):
        raise ValueError("请选择安装位置。")
    if params["brake_type"] not in ("", "鼓式", "盘式"):
        raise ValueError("请选择制动型式。")
    return {
        **params,
        "width": number(values.get("width"), "宽度", maximum=10000),
        "length": number(values.get("length"), "长度", maximum=10000),
        "holes": number(values.get("holes"), "孔数", integer=True, maximum=1000),
    }


def lookup(params):
    if any(params[key] for key in ("q", "axle", "code", "tonnage")):
        return match(**params)
    return None


@fitment_bp.get("")
def search():
    values = request.args
    try:
        params = specifications(values)
    except ValueError as exc:
        return error(str(exc))
    result = lookup(params)
    skip_code, skip_axle = (values.get(key) == "1" for key in ("skip_code", "skip_axle"))
    wizard = decide_next(**{key: params[key] for key in ("q", "axle", "code", "position", "length")},
                         result=result, skip_code=skip_code, skip_axle=skip_axle)
    if result is None and values.get("skip_vehicle") == "1":
        wizard = {"phase": "miss", "title": "信息不足", "hint": "至少提供一个件号、车桥型号或车型后再查询。"}
    if values.get("force_pick") == "1" and result and result["candidates"] and not result["assert_unique"]:
        wizard = {"phase": "pick", "title": "请选择候选", "hint": "选择后可生成待核价草稿，并继续核对旧件。"}
    return {"catalog_count": len(load_records()), "result": result, "wizard": wizard}


@fitment_bp.get("/inquiries/<int:inquiry_id>")
def inquiry_context(inquiry_id):
    row = record(inquiry_id)
    fields = leads.payload(row["fields"], {})
    message = leads.current_reply(fields.get("content", ""))
    source = f"{fields.get('title', '')}\n{message}"
    catalog = load_parts_by_code()
    catalog_codes = [token.group() for token in CATALOG_CODE.finditer(source) if _norm_code(token.group()) in catalog]
    detected = list(dict.fromkeys((*catalog_codes, *(token.group() for token in leads.PART_NUMBER.finditer(source)))))
    widths = {token[1] or token[2] for token in WIDTH.finditer(source)}
    hole_counts = {token[1] for token in HOLES.finditer(source)}
    return {
        "id": row["id"],
        "title": leads.plain(fields.get("title"), 140),
        "codes": detected[:8],
        "width": next(iter(widths)) if len(widths) == 1 and len(detected) <= 1 else "",
        "holes": next(iter(hole_counts)) if len(hole_counts) == 1 and len(detected) <= 1 else "",
    }


@fitment_bp.post("/quote")
def quote():
    values = request.form
    try:
        params = specifications(values)
        qty = number(values.get("qty"), "询价数量", integer=True, maximum=1000000)
        if qty is None:
            raise ValueError("请填写正整数询价数量。")
        unit_price = number(values.get("unit_price"), "参考单价", maximum=1000000)
        if unit_price is not None and unit_price != round(unit_price, 2):
            raise ValueError("参考单价最多保留两位小数。")
    except ValueError as exc:
        return error(str(exc))
    result = lookup(params)
    selected = next((card for card in (result or {}).get("candidates", [])
                     if card["id"] == values.get("candidate_id")), None)
    if selected is None:
        return error("该候选已变化，请重新查询并确认。")
    return {
        **build_quote(selected, qty=qty, unit_price=unit_price,
                      total_price=round(unit_price * qty, 2) if unit_price is not None else None),
        "needs_confirmation": not result["assert_unique"],
    }
