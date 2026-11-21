# -*- coding: utf-8 -*-
"""B · 询盘承接 —— B-01 粘贴与字段提取（纯函数）。

本文件刻意只放纯函数，边界写死如下：
- 不读文件、不写文件、不联网、不调模型、不注册路由、不 import flask、不引第三方依赖。
- 车型别名表一律由调用方注入（形参 alias_map）；真实 data/fitment/alias.json 目前不存在，
  所以别名表不可用时车型一律标「待联调」，本文件不内置任何车型别名、不编车型。
- 原文原样返回，换行、制表符、空格、标点都不动。

签名（D-04 定死）：
    detect_lang(text) -> "zh" | "en"
    extract_vehicle(text, alias_map) -> 标准车型名 或 ""
    extract_qty(text) -> int | None
    build_card(text, alias_map) -> dict（含 need_confirm）

数量护栏（D-05，只做 G1+G2，不做 G3）：
- G1：命中值 >= 10000 且不是「数字 x 数字 单位」三段式 → 弃用（挡 19036 sets 这类零件编号误命中）。
- G2：三段式取第二段（19036 x 250 sets -> 250）。
- G3「多候选取最小」不做：会误伤真大单，宁可 qty=None + need_confirm=True。

数量 token 完整性（本轮定死，通例不是特判）：
- 一个数量必须按完整 token 读：乘号前缀 + 符号 + 完整数字字面量 + 单位。
  `2.5` / `2,5` / `-20` / `12,000` 都是整体，绝不允许拆出 `5`、`20` 这种碎片。
- 小数（2.5）、负数（-20 / 全角负号）、逗号歧义（2,5 到底是 2.5 还是 25）、
  单位语义不明（套装/套裝/套件 不等于「套」）→ 一律 qty=None + need_confirm=True + 解释。
- 合法的整数与合法千分位（217 / 200 / 1,000）照常出数，不许被上面这些规则误伤。
- 回收扫描：任何「数字 单位」没被数量 token 认领，也必须报出来要求人工确认，
  绝不允许静默丢数（页面写「未提及」而原文里明明有数）。

标记规则（D-06 / D-07 / D-08，只加标记、不改值、不丢信息）：
- D-06 G1 弃用后一个数量都不剩 → qty=None + need_confirm=True，reason 说明哪个编号被忽略。
- D-07 值落在 1900–2100（像年份）→ qty 保留原值 + need_confirm=True，不做年份白名单。
- D-08 解析出 0 → qty=0 保留 + need_confirm=True（0 是真实值，抹成 None 才是骗人）。
- 单个普通值（如 250 sets）不得被误标：need_confirm=False。
"""

from __future__ import annotations

import re

__all__ = [
    "FITMENT_PENDING",
    "detect_lang",
    "extract_qty",
    "extract_vehicle",
    "build_card",
    "qty_label",
]

# 中文（含扩展 A）字符：出现任何一个即判为中文询盘。
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")

_UNIT = r"套装|套裝|套件|sets?|pcs?|units?|pieces?|pairs?|套|件|对"

# 单位语义不明确的写法：「套装/套裝/套件」都不等于「套」，
# 不许直接当成 200 套确认，要交人工。
_AMBIGUOUS_UNITS = ("套装", "套裝", "套件")

# 数字字面量：整数，或带任意 . / , 分隔的复合形式。
# 必须贪婪地吃掉整个数字 token——「2.5」得整体成一个 token。
# 早先的写法允许「先匹配 2 再让 5 单独溜出来」，这就是 T850x2.5 -> 5 的根因。
_NUM_LIT = r"\d+(?:(?:[.,]\d+)+)?"

# 乘号前缀（型号 x 数量）：T850x / 19036 x / FH16 x / OE12345x
_MULT_PREFIX = r"(?P<lead>[0-9A-Za-z]{1,20})\s*[xX×]"

# 完整数量 token：可选乘号前缀 + 可选正负号 + 完整数字 + 单位。
# 一条正则收全，不再让小数/负数的碎片从别的路径溜过去。
_QTY_TOKEN_RE = re.compile(
    r"(?<![0-9A-Za-z])"
    r"(?:" + _MULT_PREFIX + r")?\s*"
    r"(?P<sign>[-+])?\s*"
    r"(?P<num>" + _NUM_LIT + r")\s*"
    r"(?P<unit>" + _UNIT + r")(?![A-Za-z])",
    re.IGNORECASE,
)

# 回收扫描：任何「数字+单位」只要没被上面认领，就必须报出来。
# 宁可多问一句，也不许页面写「未提及」而原文里明明有数（x200 sets / T850 sets）。
_QTY_ANY_RE = re.compile(
    r"(?P<num>" + _NUM_LIT + r")\s*(?P<unit>" + _UNIT + r")(?![A-Za-z])",
    re.IGNORECASE,
)

# G1 阈值：命中值达到此数且非三段式，按「大数量待确认」处理（不猜、不丢信息）。
_G1_THRESHOLD = 10000

# D-07：可疑区间。值落在 1900–2100 且带单位 → 保留原值但标记人工核对。
# 不建年份白名单（永远不全、还会误伤真数量），只用一个区间提示。
_SUSPICIOUS_LOW = 1900
_SUSPICIOUS_HIGH = 2100


def _suspicion(value):
    """返回该数量值的可疑原因；不可疑返回空串。

    D-07：1900–2100 之间像年份。
    D-08：0 是真实解析值，保留但要人看一眼。
    """
    if value == 0:
        return "数量为 0，需人工确认是否真的不要货"
    if _SUSPICIOUS_LOW <= value <= _SUSPICIOUS_HIGH:
        return "该数值落在年份区间（1900–2100），需人工确认是数量还是年份"
    return ""

# 车型别名表不可用时的统一标记。
FITMENT_PENDING = "待联调"


# 全角 / 异体字符归一（只用于抽取，绝不回写原文）。
# 中文输入法、微信、Word 里粘过来的负号常常是 U+FF0D，数字也常是全角的。
# 未归一就会把「－20 sets」当成 20、「２.５ sets」当成 5——和当初 2.5→5 是同一类错。
# 映射表全部是「1 字符 → 1 字符」等长替换，所以归一后的下标与原文字符下标一一对应，
# 行号、原文切片都不会错位。
_TRANSLATE = str.maketrans(
    {
        # 各种减号
        "\uff0d": "-",  # － 全角连字符-减号
        "\ufe63": "-",  # ﹣ 小号连字符-减号
        "\u2212": "-",  # − 数学减号
        "\uff0b": "+",  # ＋
        "\uff0e": ".",  # ． 全角句点（小数点）
        "\uff58": "x",  # ｘ 全角小写 x（三段式乘号）
        "\uff38": "X",  # Ｘ 全角大写 X
        **{chr(0xFF10 + d): str(d) for d in range(10)},  # ０-９ → 0-9
    }
)

# 全角逗号「，」不能一律换成 ASCII：「产品A 200，产品B 300 sets」里它是句子逗号，
# 一律换会把两个独立数量粘成 200,300 这种假大数。只在「千分位」位置换：
# 逗号前是数字、逗号后恰好 3 位数字且再往后不是数字（12，000 → 12,000）。
_COMMA_THOUSANDS_RE = re.compile(r"(?<=\d)\uff0c(?=\d{3}(?!\d))")


def _normalize(text):
    """归一全角/异体字符，长度不变（全部等长替换）。"""
    return _COMMA_THOUSANDS_RE.sub(",", text.translate(_TRANSLATE))


def _as_text(text):
    return "" if text is None else str(text)


def _parse_num(literal):
    """把数字字面量判成 (值, 种类)。种类是 int / decimal / comma。

    判据（通例，不针对单个样例）：
    - 含 '.' → 小数（2.5、２.５ 归一后同形）
    - 含 ',' 但不是合法千分位（1,000 / 12,000,000）→ 有歧义（2,5 到底是 2.5 还是 25？）
      合法的千分位才当整数，其余一律交人工，绝不猜。
    - 纯数字 → 整数
    """
    if "." in literal:
        return None, "decimal"
    if "," in literal:
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+", literal):
            return int(literal.replace(",", "")), "int"
        return None, "comma"
    return int(literal), "int"


def _line_of(text, index):
    return text.count("\n", 0, index) + 1


def detect_lang(text):
    """含中文字符 → "zh"，否则 → "en"（空/None 按英文，符合 PRD「失败默认英文」）。"""
    text = _as_text(text)
    return "zh" if _CJK_RE.search(text) else "en"


def extract_qty(text):
    """抽取数量。

    返回 int 或 None。None 的两种含义都在 build_card 里区分：
    - 未提及（没有任何数字+单位）
    - 出现多个不同的明确数量 → 不替客户挑，交人工确认
    """
    return _qty_info(text)["qty"]


def _qty_info(text):
    """数量抽取细节（内部用）：qty / need_confirm / 候选 / 异常写法 / 大数量。

    返回键：qty, need_confirm, reason, candidates, anomalies, discarded, multi_item, big
    """
    text = _as_text(text)
    # 归一后的文本用于匹配（等长替换，下标与原文字符一一对应）；
    # 原文仍用 text 切片，所以「－20 sets」报原因时显示的是原样写法。
    norm = _normalize(text)

    # ① 完整数量 token 扫描：乘号前缀 + 符号 + 完整数字 + 单位，一次收全。
    # 关键：数字字面量必须整体吃下（2.5 / 2,5 / 12,000 都是一个 token），
    # 这样 2.5 不可能再被拆成「2」和「5」，-20 也不可能被当成 20。
    anomalies = []
    candidates = []
    discarded = []
    claimed = []      # 已认领区间（含异常写法），②的回收扫描据此判重

    for m in _QTY_TOKEN_RE.finditer(norm):
        claimed.append((m.start(), m.end()))
        shown = text[m.start():m.end()]
        line = _line_of(text, m.start())
        unit = m.group("unit")
        sign = m.group("sign")
        lead = m.group("lead")
        value, num_kind = _parse_num(m.group("num"))

        # 语义不明的一律先记成异常，后面按「留空 + 待核 + 解释」处理。
        if unit in _AMBIGUOUS_UNITS:
            anomalies.append(
                {
                    "raw": shown,
                    "value": m.group("num"),
                    "kind": "unit",
                    "line": line,
                    "reason": "单位不明确（%s）：「%s」不等于「套」，需人工确认"
                    % (shown, unit),
                }
            )
            continue
        if sign == "-":
            anomalies.append(
                {
                    "raw": shown,
                    "value": m.group("num"),
                    "kind": "negative",
                    "line": line,
                    "reason": "数量为负数（%s），需人工确认是否写错" % shown,
                }
            )
            continue
        if sign == "+":
            anomalies.append(
                {
                    "raw": shown,
                    "value": m.group("num"),
                    "kind": "sign",
                    "line": line,
                    "reason": "数量前带正号（%s），需人工确认是否为误输入" % shown,
                }
            )
            continue
        if num_kind == "decimal":
            anomalies.append(
                {
                    "raw": shown,
                    "value": m.group("num"),
                    "kind": "decimal",
                    "line": line,
                    "reason": "不是整数套数（%s），需人工确认" % shown,
                }
            )
            continue
        if num_kind == "comma":
            anomalies.append(
                {
                    "raw": shown,
                    "value": m.group("num"),
                    "kind": "comma",
                    "line": line,
                    "reason": "数字写法有歧义（%s）：逗号可能是小数点也可能是千分位，需人工确认"
                    % shown,
                }
            )
            continue

        if lead:
            candidates.append(
                {
                    "value": value,
                    "unit": unit,
                    "raw": shown,
                    "line": line,
                    "source": "triple",
                }
            )
            continue

        if value >= _G1_THRESHOLD:
            discarded.append({"value": value, "unit": unit, "raw": shown, "line": line})
            continue

        candidates.append(
            {"value": value, "unit": unit, "raw": shown, "line": line, "source": "plain"}
        )

    distinct = []
    for c in candidates:
        if c["value"] not in distinct:
            distinct.append(c["value"])

    # ② 回收扫描：任何「数字 单位」没被①认领，一律记下来要求人工确认。
    # 宁可多问一句，也不许页面写「未提及」而原文里明明有数（x200 sets / T850 sets）。
    # 判重必须用「区间重叠」：`Box 2 sets` 里单词尾字母 x 的起点在数字之前，用起点判会误报。
    glued = []
    for m in _QTY_ANY_RE.finditer(norm):
        if any(s < m.end() and m.start() < e for s, e in claimed):
            continue
        glued.append(
            {
                "value": m.group("num"),
                "unit": m.group("unit"),
                "raw": text[m.start():m.end()],
                "line": _line_of(text, m.start()),
            }
        )

    def _pack(qty, need_confirm, reason, multi_item=False):
        return {
            "qty": qty,
            "need_confirm": need_confirm,
            "reason": reason,
            "candidates": candidates,
            "anomalies": anomalies,
            "discarded": discarded,
            "glued": glued,
            "multi_item": multi_item,
            "big": bool(discarded),
        }

    # ④ 多个数量：不替客户挑，也不把某一个当总量。
    if len(candidates) > 1:
        detail = "、".join(
            "%d %s（第 %d 行）" % (c["value"], c["unit"], c["line"]) for c in candidates
        )
        if len(distinct) > 1:
            head = "出现多个不同的数量，需人工确认："
        else:
            head = "多个品项，需人工确认（不能把 %d 当总量）：" % distinct[0]
        extra = []
        if anomalies:
            extra.append("；".join(a["reason"] for a in anomalies))
        if discarded:
            extra.append(
                "另有疑似编号或大数量已忽略："
                + "、".join("%s（第 %d 行）" % (d["raw"], d["line"]) for d in discarded)
            )
        if glued:
            extra.append(
                "另有「x 数量」写法无法确定归属："
                + "、".join("%s（第 %d 行）" % (g["raw"], g["line"]) for g in glued)
            )
        reason = head + detail + ("；" + "；".join(extra) if extra else "")
        return _pack(None, True, reason, multi_item=True)

    # ⑤ 没有任何可用的数量。
    if not candidates:
        notes = []
        if anomalies:
            notes.append("；".join(a["reason"] for a in anomalies))
        if discarded:
            notes.append(
                "大数量待确认："
                + "、".join("%s（第 %d 行）" % (d["raw"], d["line"]) for d in discarded)
                + "，请核对是否为大单或零件编号"
            )
        if glued:
            notes.append(
                "数量写法待确认："
                + "、".join("%s（第 %d 行）" % (g["raw"], g["line"]) for g in glued)
                + "，请核对是数量还是零件编号的一部分"
            )
        if not notes:
            return _pack(None, False, "")
        return _pack(None, True, "；".join(notes))

    # ⑥ 唯一数量：可疑的照取但标出来，异常的另说。
    first = candidates[0]
    notes = []
    suspicious = _suspicion(first["value"])   # D-07 / D-08：单个值可疑 → 保留 + 标记
    if suspicious:
        notes.append("「%s」%s" % (first["raw"], suspicious))
    if anomalies:
        notes.append("；".join(a["reason"] for a in anomalies))
    if discarded:
        notes.append(
            "另有疑似编号或大数量已忽略："
            + "、".join("%s（第 %d 行）" % (d["raw"], d["line"]) for d in discarded)
        )
    if glued:
        notes.append(
            "另有「x 数量」写法无法确定归属："
            + "、".join("%s（第 %d 行）" % (g["raw"], g["line"]) for g in glued)
        )

    return _pack(first["value"], bool(notes), "；".join(notes))


def _alias_pairs(alias_map):
    """把注入的别名表摊平成 [(别名, 标准车型名), ...]；不可用返回 None。

    兼容两种表形：
      {"天锦KR": "东风天锦KR", ...}          # 别名 -> 标准名
      {"东风天锦KR": ["天锦KR", ...], ...}   # 标准名 -> 别名列表
    """
    if not isinstance(alias_map, dict) or not alias_map:
        return None

    pairs = []
    for key, value in alias_map.items():
        if isinstance(value, (list, tuple)):
            canonical = str(key).strip()
            if not canonical:
                continue
            for alias in value:
                if isinstance(alias, str) and alias.strip():
                    pairs.append((alias.strip(), canonical))
        elif isinstance(value, str):
            alias = str(key).strip()
            canonical = value.strip()
            if alias and canonical:
                pairs.append((alias, canonical))
    return pairs or None


def _is_word_alias(alias):
    """纯 ASCII 别名按整词匹配，其余（中文等）按子串匹配。"""
    return bool(re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z .\-]*", alias))


def _vehicle_info(text, alias_map):
    text = _as_text(text)
    pairs = _alias_pairs(alias_map)

    if pairs is None:
        return {
            "vehicle": "",
            "alias": "",
            "status": FITMENT_PENDING,
            "note": "车型别名表未就绪（data/fitment/alias.json 不存在），车型待联调",
        }

    pairs = sorted(pairs, key=lambda p: len(p[0]), reverse=True)  # 长别名优先
    hits = []
    for alias, canonical in pairs:
        if _is_word_alias(alias):
            pattern = r"(?<![0-9A-Za-z])" + re.escape(alias) + r"(?![0-9A-Za-z])"
            if re.search(pattern, text, re.IGNORECASE):
                hits.append((alias, canonical))
        elif alias.casefold() in text.casefold():
            hits.append((alias, canonical))

    if not hits:
        return {
            "vehicle": "",
            "alias": "",
            "status": "",
            "note": "别名表里没有命中，车型未识别",
        }

    longest = len(hits[0][0])
    top = [h for h in hits if len(h[0]) == longest]
    canonicals = []
    for _, canonical in top:
        if canonical not in canonicals:
            canonicals.append(canonical)

    if len(canonicals) > 1:
        return {
            "vehicle": "",
            "alias": "",
            "status": "",
            "note": "别名命中多个不同车型，需人工确认：" + "、".join(canonicals),
        }

    return {"vehicle": canonicals[0], "alias": top[0][0], "status": "", "note": ""}


def extract_vehicle(text, alias_map=None):
    """按调用方注入的别名表识别车型，返回标准车型名；未命中/歧义/表缺失返回 ""。"""
    return _vehicle_info(text, alias_map)["vehicle"]


def build_card(text, alias_map=None):
    """一条询盘原文 → 意向卡字段。

    返回 dict：
      text / lang / vehicle / qty   —— 卡片四字段（与 PRD 数据文件同名，落盘只取这四个）
      need_confirm                  —— 只在返回值里，不落盘（D-02）
      need_confirm_reason           —— 一句人话，给页面直接显示
      qty_candidates / discarded_numbers / vehicle_note / vehicle_status —— 调试与人工核对用
    """
    raw = _as_text(text)
    qty_info = _qty_info(raw)
    vehicle_info = _vehicle_info(raw, alias_map)

    return {
        "text": raw,
        "lang": detect_lang(raw),
        "vehicle": vehicle_info["vehicle"],
        "qty": qty_info["qty"],
        "need_confirm": qty_info["need_confirm"],
        "need_confirm_reason": qty_info["reason"],
        "qty_candidates": qty_info["candidates"],
        "qty_anomalies": qty_info["anomalies"],
        "discarded_numbers": qty_info["discarded"],
        "glued_numbers": qty_info["glued"],
        "multi_item": qty_info["multi_item"],
        "big_qty": qty_info["big"],
        "vehicle_note": vehicle_info["note"],
        "vehicle_status": vehicle_info["status"],
    }


def qty_label(card):
    """数量该怎么显示（页面用，纯函数）。返回 (文字, 是否待核)。

    - 有唯一值 → 数字
    - 多个数量 → 「多个品项待核」（不许写成「未提及」，也不许把某一个当总量）
    - 大数量被忽略 → 「大数量待确认」
    - 小数/负数等异常 → 「数量待确认」
    - 粘连的 x 数量 → 「数量待确认」（不许写「未提及」）
    - 真的没有 → 「未提及」
    """
    if card.get("qty") is not None:
        return str(card["qty"]), bool(card.get("need_confirm"))
    if card.get("multi_item"):
        return "多个品项待核", True
    if card.get("big_qty"):
        return "大数量待确认", True
    if card.get("qty_anomalies") or card.get("glued_numbers"):
        return "数量待确认", True
    return "未提及", False
