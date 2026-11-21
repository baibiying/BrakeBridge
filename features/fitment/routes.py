from __future__ import annotations

from flask import Blueprint, render_template, request
from urllib.parse import urlencode

from features.fitment.match import match
from features.fitment.quote import _money_text, build_quote, price_bounds
from features.fitment.wizard import (
    ask_axle,
    ask_code_first,
    ask_vehicle_first,
    decide_next,
)

fitment_bp = Blueprint(
    "fitment",
    __name__,
    template_folder="templates",
)


def _parse_float(val: str | None) -> float | None:
    if val is None or str(val).strip() == "":
        return None
    try:
        return float(str(val).strip())
    except ValueError:
        return None


def _parse_money(val: str | None) -> float | None:
    if val is None or str(val).strip() == "":
        return None
    try:
        amount = float(str(val).strip())
    except ValueError:
        return None
    if amount < 0:
        return None
    return amount


def _parse_int(val: str | None) -> int | None:
    if val is None or str(val).strip() == "":
        return None
    try:
        return int(str(val).strip())
    except ValueError:
        return None


def _flag(name: str) -> bool:
    return (request.args.get(name) or "").strip() in ("1", "true", "yes")


def _qs_merge(extra: dict | None = None, **kwargs) -> str:
    """从当前请求合并参数，生成查询串（用于追问跳转）。"""
    base = {
        "q": (request.args.get("q") or "").strip(),
        "axle": (request.args.get("axle") or "").strip(),
        "code": (request.args.get("code") or "").strip(),
        "year": (request.args.get("year") or "").strip(),
        "brake_type": (request.args.get("brake_type") or "").strip(),
        "tonnage": (request.args.get("tonnage") or "").strip(),
        "position": (request.args.get("position") or "").strip(),
        "width": (request.args.get("width") or "").strip(),
        "length": (request.args.get("length") or "").strip(),
        "holes": (request.args.get("holes") or "").strip(),
        "mode": (request.args.get("mode") or "").strip(),
        "from": (request.args.get("from") or "").strip(),
        "skip_code": (request.args.get("skip_code") or "").strip(),
        "skip_axle": (request.args.get("skip_axle") or "").strip(),
        "qty": (request.args.get("qty") or "").strip(),
        "unit": (request.args.get("unit") or "").strip(),
        "total": (request.args.get("total") or "").strip(),
    }
    merged = {}
    if extra:
        merged.update(extra)
    merged.update(kwargs)
    for k, v in merged.items():
        if v is None or v == "":
            base[k] = ""
        else:
            base[k] = str(v)
    cleaned = {k: v for k, v in base.items() if v not in (None, "")}
    # wiz / force_pick / pick：仅当本次显式传入时保留
    if merged.get("wiz"):
        cleaned["wiz"] = str(merged["wiz"])
    if merged.get("force_pick"):
        cleaned["force_pick"] = str(merged["force_pick"])
    if merged.get("pick"):
        cleaned["pick"] = str(merged["pick"])
    return urlencode(cleaned)


def _qs_only(extra: dict | None = None, **kwargs) -> str:
    """不合并当前 URL，只按给定参数生成（用于返回上一步）。"""
    merged = {}
    if extra:
        merged.update(extra)
    merged.update(kwargs)
    mode = (request.args.get("mode") or "").strip()
    if mode and "mode" not in merged:
        merged["mode"] = mode
    cleaned = {k: str(v) for k, v in merged.items() if v not in (None, "")}
    return urlencode(cleaned)


@fitment_bp.get("/fitment")
def fitment_home():
    q = (request.args.get("q") or "").strip()
    axle = (request.args.get("axle") or "").strip()
    code = (request.args.get("code") or "").strip()
    year = (request.args.get("year") or "").strip()
    brake_type = (request.args.get("brake_type") or "").strip()
    tonnage = (request.args.get("tonnage") or "").strip()
    position = (request.args.get("position") or "").strip() or None
    if position not in ("前", "后", None):
        position = None
    if brake_type not in ("鼓式", "盘式", ""):
        brake_type = ""
    width = _parse_float(request.args.get("width"))
    length = _parse_float(request.args.get("length"))
    holes = _parse_int(request.args.get("holes"))
    mode = (request.args.get("mode") or "").strip()
    wiz = (request.args.get("wiz") or "").strip()
    skip_code = _flag("skip_code")
    skip_axle = _flag("skip_axle")
    skip_vehicle = _flag("skip_vehicle")
    force_pick = _flag("force_pick")
    from_raw = (request.args.get("from") or "").strip()
    source_label = None
    if from_raw.startswith("inquiry:"):
        source_label = f"来源评论 #{from_raw.split(':', 1)[1]}"

    selected_id = (request.args.get("pick") or "").strip() or None
    qty = _parse_int(request.args.get("qty"))
    if qty is not None and qty <= 0:
        qty = None
    unit_price = _parse_money(request.args.get("unit"))
    total_price = _parse_money(request.args.get("total"))
    unit_error = ""
    unit_rejected = ""
    draft = None
    picked_card = None
    need_qty = False
    result = None

    has_query = bool(q or axle or code or tonnage)
    if has_query:
        result = match(
            q=q,
            position=position,
            width=width,
            holes=holes,
            length=length,
            axle=axle or None,
            code=code or None,
            year=year or None,
            brake_type=brake_type or None,
            tonnage=tonnage or None,
        )
        if selected_id and result and not result.get("miss"):
            for card in result["candidates"]:
                if card["id"] == selected_id:
                    picked_card = card
                    break
        elif (
            result
            and result.get("assert_unique")
            and result.get("candidates")
            and request.args.get("draft") == "1"
        ):
            picked_card = result["candidates"][0]
            selected_id = picked_card["id"]

        if picked_card:
            if qty:
                bounds = price_bounds(picked_card.get("price_range_usd"))
                if unit_price is not None and bounds and not (bounds[0] <= unit_price <= bounds[1]):
                    unit_rejected = _money_text(unit_price)
                    unit_error = f"单价需在价区 USD { _money_text(bounds[0]) }–{ _money_text(bounds[1]) } 内"
                    unit_price = None
                    total_price = None
                elif unit_price is not None and total_price is None:
                    total_price = round(unit_price * qty, 2)
                draft = build_quote(
                    picked_card,
                    qty=qty,
                    unit_price=unit_price,
                    total_price=total_price,
                )
            else:
                need_qty = True

    # —— 渐进追问 ——
    answered_seed = []
    if skip_code and not code:
        answered_seed.append(("编码", "不知道"))
    if skip_axle and not axle:
        answered_seed.append(("车桥", "不知道"))

    if not has_query and (wiz == "stuck" or skip_vehicle):
        # 三问都「不知道」→ 停在信息不足，绝不退回第一问
        wizard = {
            "phase": "miss",
            "title": "信息不足",
            "hint": "编码、车桥、车型都未知时无法定片，请至少提供其中一项后重试",
            "answered": answered_seed
            + ([("车型", "不知道")] if skip_vehicle else []),
            "done": False,
            "nearest": [],
            "back": {
                "label": "← 返回上一步",
                "params": {"skip_code": "1", "skip_axle": "1", "wiz": "vehicle"},
            },
        }
    elif not has_query and wiz == "vehicle":
        # 必须带 wiz=vehicle 才进入第三问（避免刷新/残留 skip_* 误开中间步）
        wizard = ask_vehicle_first(answered_seed)
    elif not has_query and wiz == "axle":
        wizard = ask_axle(answered_seed, skip_code=True)
    elif not has_query:
        # 裸开 /fitment 或只有残留 skip_* → 一律第一问
        wizard = ask_code_first([])
    else:
        wizard = decide_next(
            q=q,
            axle=axle,
            code=code,
            position=position,
            length=length,
            result=result,
            skip_code=skip_code,
            skip_axle=skip_axle,
        )

    if force_pick and result and not result.get("miss") and not result.get("assert_unique"):
        wizard = {
            **wizard,
            "phase": "pick",
            "done": False,
            "show_candidates": True,
            "title": "请勾选最接近的一条",
            "hint": "你选择了「不知道」，先列出候选供确认。",
        }

    if draft:
        wizard = {
            **wizard,
            "phase": "done",
            "done": True,
            "show_candidates": True,
            "title": "报价依据已生成",
            "hint": f"数量 {qty} 套 · 单价和总价可直接填写。",
        }
    elif need_qty and picked_card:
        wizard = {
            **wizard,
            "phase": "qty",
            "done": False,
            "show_candidates": True,
            "title": "填写询价数量",
            "hint": "报价依据需要销售当场填写个数，填完再出草稿。",
        }

    return render_template(
        "fitment/index.html",
        title="车型速配",
        active="fitment",
        q=q,
        axle=axle,
        code=code,
        year=year,
        brake_type=brake_type,
        tonnage=tonnage,
        position=position or "",
        width=width if width is not None else "",
        length=length if length is not None else "",
        holes=holes if holes is not None else "",
        mode=mode,
        skip_code=skip_code,
        skip_axle=skip_axle,
        source_label=source_label,
        result=result,
        draft=draft,
        picked_card=picked_card,
        need_qty=need_qty,
        qty=qty if qty is not None else "",
        selected_id=selected_id,
        wizard=wizard,
        unit_error=unit_error,
        unit_rejected=unit_rejected,
        price_range=(picked_card or {}).get("price_range_usd") or "—",
        price_min=_money_text(price_bounds((picked_card or {}).get("price_range_usd"))[0]) if picked_card and price_bounds((picked_card or {}).get("price_range_usd")) else "",
        price_max=_money_text(price_bounds((picked_card or {}).get("price_range_usd"))[1]) if picked_card and price_bounds((picked_card or {}).get("price_range_usd")) else "",
        qs=_qs_merge,
        qs_only=_qs_only,
    )
