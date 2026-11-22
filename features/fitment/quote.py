"""报价草稿：成交价恒为空；价区仅示意；明确输出「报价依据」。"""

from __future__ import annotations


def price_bounds(price_range: str | None) -> tuple[float, float] | None:
    """价区文本如 12–18 → (12, 18)。没有区间时不限制。"""
    text = (price_range or "").strip().replace("–", "-").replace("－", "-")
    if not text or text in {"—", "-"}:
        return None
    parts = [p.strip() for p in text.split("-")]
    if len(parts) != 2:
        return None
    try:
        low = float(parts[0])
        high = float(parts[1])
    except ValueError:
        return None
    if low > high:
        low, high = high, low
    return low, high


def _money_text(amount: float | None) -> str:
    if amount is None:
        return ""
    text = f"{amount:.2f}".rstrip("0").rstrip(".")
    return text


def build_quote(
    card: dict,
    qty: int | None = None,
    unit_price: float | None = None,
    total_price: float | None = None,
) -> dict:
    rec = card.get("record") or card
    size = rec.get("size_raw") or "—"
    src = rec.get("src") or "—"
    page = rec.get("page")
    cell = rec.get("cell") or "—"
    vehicle = rec.get("vehicle_raw") or rec.get("vehicle_norm") or "—"
    brand = rec.get("brand") or "—"
    position = rec.get("position") or "—"
    variant = rec.get("variant") or "—"
    rid = rec.get("id") or "—"

    master_id = card.get("master_id") or rid
    alt_codes = card.get("alt_codes") or []
    code_map_lines = card.get("code_map_lines") or []
    price_range = card.get("price_range_usd") or "—"
    bounds = price_bounds(price_range)
    material = card.get("material_grade") or "待企业确认"
    report_id = card.get("report_id") or "TR-2024-XXXX（占位）"
    why = card.get("why_line") or ""

    axle = " · ".join(
        p
        for p in [
            rec.get("vehicle_norm") or vehicle,
            (position + "桥") if position and position != "—" else None,
            (f"{variant}版" if variant and variant != "—" else None),
        ]
        if p
    )

    qty_value = f"{qty} 套" if qty and qty > 0 else "—"
    code_chips = []
    for row in card.get("code_map") or []:
        code = (row.get("code") or "").strip()
        if code:
            code_chips.append({"code": code, "source": (row.get("source") or "").strip()})
    if not code_chips:
        code_chips = [{"code": c, "source": ""} for c in alt_codes if c]

    basis = [
        {
            "label": "具体车桥（1:1）",
            "value": axle,
            "hint": "一条具体车桥固定对应一片；换桥须重新选型",
        },
        {
            "label": "主件号",
            "value": master_id,
            "hint": "冠英侧一物一号；对外可对照一物多码",
        },
        {
            "label": "一物多码",
            "value": (
                "；".join(code_map_lines)
                if code_map_lines
                else (" / ".join(alt_codes) if alt_codes else "—")
            ),
            "hint": "跨主机厂码不同、同企渠道/工厂码也可能不同；任一码 → 同一主件号。非 OE 库直连",
        },
        {
            "label": "询价数量",
            "value": qty_value,
            "hint": "由销售当场填写；未填则无法出可审草稿",
        },
        {
            "label": "适配规格",
            "value": size,
            "hint": "长×宽×厚，与旧件或图纸核对",
        },
        {
            "label": "目录证据",
            "value": f"{src} · p{page} · 单元格 {cell} · 条目 {rid}",
            "hint": "可翻回产品册原页，防止口误",
        },
        {
            "label": "为什么是这几个",
            "value": why or f"车桥「{axle}」1:1 定片 + 目录页码",
            "hint": "评审口径：讲清依据，不是猜",
        },
        {
            "label": "安装位置 / 版本",
            "value": f"位置 {position} ｜ 版本 {variant} ｜ 品牌册 {brand}",
            "hint": "前/后、A/B 视为不同车桥",
        },
        {
            "label": "材质档",
            "value": material,
            "hint": "日均询价里最耗时的核对项之一",
        },
        {
            "label": "试验报告",
            "value": report_id,
            "hint": "有产品、有报告库；结构化挂载后可自动带出",
        },
        {
            "label": "价区（示意）",
            "value": f"USD {price_range}" if price_range != "—" else "待核价",
            "hint": "仅示意区间，不是成交锁价；单价总价仍空",
        },
        {
            "label": "认证 / 条款",
            "value": "待客户确认（如 ECE R90、目的港、MOQ）",
            "hint": "进报价函前由销售勾选",
        },
    ]

    return {
        "title": "报价草稿（待核价）",
        "vehicle": vehicle,
        "spec_check": f"规格核对：{size}",
        "evidence": f"目录依据：{src} · p{page} · 单元格 {cell}",
        "material": f"材质：{material}",
        "report": f"试验报告：{report_id}",
        "basis": basis,
        "qty": qty if qty and qty > 0 else None,
        "qty_label": qty_value,
        "axle": axle,
        "size": size,
        "code_chips": code_chips,
        "price_range": price_range,
        "material_grade": material,
        "report_id": report_id,
        "why": why,
        "evidence_short": f"{src} · p{page}",
        "unit_price": _money_text(unit_price),
        "total_price": _money_text(total_price),
        "price_min": "" if bounds is None else _money_text(bounds[0]),
        "price_max": "" if bounds is None else _money_text(bounds[1]),
        "note": "成交单价须落在价区内。总价默认为单价 × 数量，也可手改。",
        "id": rid,
        "master_id": master_id,
    }
