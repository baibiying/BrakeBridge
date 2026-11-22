"""车型速配：归一化 + 召回 + 过滤 + 置信度。候选只来自 fitment.jsonl。"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "fitment"
JSONL_PATH = DATA_DIR / "fitment.jsonl"
ALIAS_PATH = DATA_DIR / "alias.json"
CROSSWALK_PATH = DATA_DIR / "crosswalk.json"
PARTS_MASTER_PATH = DATA_DIR / "parts_master.json"

ABS_RE = re.compile(r"[\(（]\s*ABS\s*[\)）]", re.I)
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
        "⾦": "金",
        "⽥": "田",
    }
)


def _half_width(s: str) -> str:
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


def normalize_query(text: str) -> tuple[str, str | None, str | None]:
    """返回 (vehicle_norm_query, position, variant)。"""
    raw = (text or "").translate(EXTRA_VARIANTS)
    raw = _half_width(raw)
    raw = raw.replace(" ", "").replace("\u3000", "")
    work = ABS_RE.sub("", raw).strip()

    position = None
    variant = None
    changed = True
    while changed and work:
        changed = False
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
        if m and len(work) > 1 and re.search(r"[\u4e00-\u9fff\d]$", work[:-1]):
            variant = m.group(1).upper()
            work = work[: m.start()]
            changed = True
            continue
        if work.endswith("改型"):
            work = work[:-2]
            changed = True
            continue
    return work.strip(), position, variant


@lru_cache(maxsize=1)
def load_records() -> list[dict]:
    if not JSONL_PATH.exists():
        return []
    rows = []
    with JSONL_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


@lru_cache(maxsize=1)
def load_alias() -> dict[str, list[str]]:
    if not ALIAS_PATH.exists():
        return {}
    with ALIAS_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    # normalize keys and alias values for lookup
    out: dict[str, list[str]] = {}
    for k, vals in data.items():
        nk, _, _ = normalize_query(k)
        out[nk] = [normalize_query(v)[0] for v in vals]
    return out


@lru_cache(maxsize=1)
def load_crosswalk() -> dict[str, dict]:
    if not CROSSWALK_PATH.exists():
        return {}
    with CROSSWALK_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    codes = data.get("codes") or {}
    # normalize keys: upper + unify ×
    out: dict[str, dict] = {}
    for k, v in codes.items():
        if k.startswith("_"):
            continue
        key = _norm_code(k)
        out[key] = v
    return out


@lru_cache(maxsize=1)
def load_parts_master() -> dict[str, dict]:
    """按 size_raw 索引的一物多码主数据。"""
    if not PARTS_MASTER_PATH.exists():
        return {}
    with PARTS_MASTER_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    by_size: dict[str, dict] = {}
    for p in data.get("parts") or []:
        size = p.get("size_raw")
        if size:
            by_size[size] = p
    return by_size


@lru_cache(maxsize=1)
def load_parts_by_code() -> dict[str, dict]:
    """任一外部码 / master_id → 同一 part（一物多码）。"""
    out: dict[str, dict] = {}
    for p in load_parts_master().values():
        mid = p.get("master_id")
        if mid:
            out[_norm_code(mid)] = p
        for c in p.get("alt_codes") or []:
            out[_norm_code(str(c))] = p
        for row in p.get("code_map") or []:
            c = row.get("code")
            if c:
                out[_norm_code(str(c))] = p
    return out


def _all_codes_of(part: dict) -> list[str]:
    """扁平多码列表（去重，保序）。"""
    seen: set[str] = set()
    out: list[str] = []
    for c in part.get("alt_codes") or []:
        s = str(c).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    for row in part.get("code_map") or []:
        s = str(row.get("code") or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _code_map_lines(part: dict) -> list[str]:
    rows = part.get("code_map") or []
    if not rows:
        return [f"{c}（对照码）" for c in (part.get("alt_codes") or [])[:6]]
    lines = []
    for row in rows:
        code = row.get("code") or ""
        src = row.get("source") or ""
        note = row.get("note") or ""
        bit = f"{code} · {src}" if src else code
        if note:
            bit = f"{bit}（{note}）"
        lines.append(bit)
    return lines


def _norm_code(code: str) -> str:
    s = (code or "").strip()
    s = _half_width(s)
    s = s.replace(" ", "").replace("×", "x").replace("X", "x")
    return s.upper()


def _recall_by_code(code: str, records: list[dict]) -> tuple[list[dict], str | None]:
    """现有编码召回：一物多码 / crosswalk / 条目 id / oe[] / 尺寸串。返回 (rows, hit_note)。"""
    if not code or not code.strip():
        return [], None
    key = _norm_code(code)

    # 0) 一物多码 parts_master（跨厂 / 同企多码 → 同一 master_id）
    part = load_parts_by_code().get(key)
    if part and part.get("size_raw"):
        size = part["size_raw"]
        rows = [r for r in records if r.get("size_raw") == size and r.get("page")]
        if rows:
            mid = part.get("master_id") or size
            siblings = [c for c in _all_codes_of(part) if _norm_code(c) != key][:3]
            sib = ("；同片还可对照 " + " / ".join(siblings)) if siblings else ""
            return rows, f"一物多码「{code.strip()}」→ 主件号 {mid} → 尺寸 {size}{sib}"

    cw = load_crosswalk()
    hit = cw.get(key)

    # 1) crosswalk
    if hit:
        if hit.get("id"):
            rows = [r for r in records if r.get("id") == hit["id"] and r.get("page")]
            if rows:
                return rows, f"跨码表命中编码 → 条目 {hit['id']}"
        if hit.get("size_raw"):
            size = hit["size_raw"]
            rows = [r for r in records if r.get("size_raw") == size and r.get("page")]
            if rows:
                return rows, f"跨码表命中编码 → 尺寸 {size}"

    # 2) 目录条目 id
    rows = [r for r in records if r.get("id") and r["id"].upper() == key and r.get("page")]
    if rows:
        return rows, f"目录条目 ID 命中 {rows[0]['id']}"

    # 3) oe 数组
    rows = []
    for r in records:
        for oe in r.get("oe") or []:
            if _norm_code(str(oe)) == key:
                rows.append(r)
                break
    if rows:
        return [r for r in rows if r.get("page")], "条目 oe 字段命中"

    # 4) 尺寸串直接当编码
    size_key = key.replace("X", "x")
    rows = []
    for r in records:
        raw = (r.get("size_raw") or "").replace("×", "x").replace("X", "x").replace(" ", "")
        if raw.upper() == size_key and r.get("page"):
            rows.append(r)
    if rows:
        return rows, f"尺寸串当作编码命中 {rows[0].get('size_raw')}"

    return [], None


def _recall_by_axle(axle: str, records: list[dict]) -> list[dict]:
    """车桥入口：在 vehicle_raw / vehicle_norm 中包含车桥关键字。"""
    if not axle or not axle.strip():
        return []
    tip, _, _ = normalize_query(axle)
    if not tip:
        return []
    out = []
    for r in records:
        if not r.get("page"):
            continue
        blob = f"{r.get('vehicle_raw') or ''}{r.get('vehicle_norm') or ''}"
        blob_n, _, _ = normalize_query(blob)
        if tip in blob_n or tip in (r.get("vehicle_norm") or ""):
            out.append(r)
    # 别名：车桥俗称也可能在 alias 里
    for canon in _alias_targets(tip):
        for r in records:
            if r.get("vehicle_norm") == canon and r.get("page"):
                out.append(r)
    seen = set()
    uniq = []
    for r in out:
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        uniq.append(r)
    return uniq


def _alias_targets(norm_q: str) -> list[str]:
    """别名命中：返回应对齐的 vehicle_norm 列表。"""
    alias = load_alias()
    targets = []
    # 键是 vehicle_norm，值是别名
    for canon, aliases in alias.items():
        if norm_q == canon or norm_q in aliases:
            targets.append(canon)
    return list(dict.fromkeys(targets))


# 演示关联：天锦KR 的后桥在目录里写作「东风天锦后」
RELATED_EXTRA: dict[str, list[tuple[str, dict]]] = {
    "东风天锦KR": [("东风天锦", {"position": "后"})],
}


def _expand_pool(records: list[dict], targets: list[str]) -> list[dict]:
    pool = [r for r in records if r.get("vehicle_norm") in targets and r.get("page")]
    for t in targets:
        for extra_norm, constr in RELATED_EXTRA.get(t, []):
            for r in records:
                if r.get("vehicle_norm") != extra_norm or not r.get("page"):
                    continue
                ok = True
                for k, v in constr.items():
                    if r.get(k) != v:
                        ok = False
                        break
                if ok:
                    pool.append(r)
    # 稳定去重
    seen = set()
    out = []
    for r in pool:
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        out.append(r)
    return out


def _dedupe_by_size(rows: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for r in rows:
        key = r.get("size_raw") or r["id"]
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _all_norms() -> list[str]:
    norms = []
    for r in load_records():
        n = r.get("vehicle_norm")
        if n and n not in norms:
            norms.append(n)
    return norms


def nearest_norms(norm_q: str, k: int = 3) -> list[str]:
    if not norm_q:
        return _all_norms()[:k]
    scored = []
    for n in _all_norms():
        ratio = SequenceMatcher(None, norm_q, n).ratio()
        # bonus if substring
        if norm_q in n or n in norm_q:
            ratio = max(ratio, 0.75)
        scored.append((ratio, n))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [n for _, n in scored[:k]]


def _fmt_size(rec: dict) -> str:
    if rec.get("size_raw"):
        return rec["size_raw"]
    return "—"


def _spec_line(rec: dict) -> str:
    size = _fmt_size(rec)
    holes = rec.get("holes")
    pitch = rec.get("pitch")
    pieces = rec.get("pieces")
    h = holes if holes is not None else "—"
    p = pitch if pitch is not None else "—"
    pc = pieces if pieces is not None else "—"
    return f"规格：{size} ｜ 孔数 {h} ｜ 孔距 {p} ｜ 片数 {pc}"


def _evidence_line(rec: dict) -> str:
    cell = rec.get("cell") or ""
    # cell like c1r6 → 第 1 列第 6 行
    m = re.match(r"c(\d+)r(\d+)", cell)
    if m:
        col_i, row_i = m.group(1), m.group(2)
        where = f"第 {col_i} 列第 {row_i} 行"
    else:
        where = cell or "—"
    return f"证据：{rec.get('src')} p{rec.get('page')} · {where}"


def _pending_line(rec: dict) -> str:
    # 有 t1/t2 四分尺寸时，第 3、4 个数字语义待核
    if rec.get("t1") is not None and rec.get("size_raw") and "/" in (rec.get("size_raw") or ""):
        return "待核项：第 3、4 个数字语义待企业确认"
    if rec.get("status") == "待核":
        return "待核项：车型名或尺寸待企业确认"
    if rec.get("status") == "name_only":
        return "待核项：缺尺寸，待企业确认"
    return "待核项：—"


def axle_key(rec: dict) -> str:
    """具体车桥键：车桥型号 + 前/后 + A/B（A/B 视为不同桥）。1:1 定片。"""
    return "|".join(
        [
            (rec.get("vehicle_norm") or "").strip(),
            (rec.get("position") or "").strip(),
            (rec.get("variant") or "").strip().upper(),
        ]
    )


def axle_label(rec: dict) -> str:
    parts = [rec.get("vehicle_norm") or rec.get("vehicle_raw") or "未命名车桥"]
    if rec.get("position"):
        parts.append(rec["position"] + "桥")
    if rec.get("variant"):
        parts.append(f"{rec['variant']}版")
    return " · ".join(parts)


def _part_for_record(rec: dict) -> dict | None:
    size = rec.get("size_raw")
    if not size:
        return None
    return load_parts_master().get(size)


def _why_line(
    rec: dict,
    level: str,
    reason: str,
    part: dict | None,
    entry: str | None = None,
    code: str | None = None,
    vehicle: str | None = None,
    possible_axle: bool = False,
) -> str:
    if entry == "code" and (code or "").strip():
        return f"编码「{code.strip()}」已精确定位到这一片"
    if entry == "axle":
        return f"车桥「{axle_label(rec)}」一一对应这一片刹车片"
    if possible_axle:
        name = (vehicle or rec.get("vehicle_norm") or "该车型").strip()
        return f"车型「{name}」可能使用车桥「{axle_label(rec)}」"
    bits = [
        f"车桥「{axle_label(rec)}」与片 1:1",
        _evidence_line(rec).replace("证据：", "目录 "),
    ]
    if part:
        if part.get("history_note"):
            bits.append(f"历史询价：{part['history_note']}")
        codes = _all_codes_of(part)
        if codes:
            bits.append(
                "一物多码（跨厂/同企多码同片）：" + " / ".join(codes[:4])
            )
    bits.append(f"置信度 {level}（{reason}）")
    return "；".join(bits)


def card_from_record(
    rec: dict,
    level: str,
    reason: str,
    entry: str | None = None,
    code: str | None = None,
    vehicle: str | None = None,
    possible_axle: bool = False,
) -> dict:
    part = _part_for_record(rec)
    master_id = (part or {}).get("master_id") or rec.get("id")
    alt_codes = _all_codes_of(part) if part else []
    code_map = list((part or {}).get("code_map") or [])
    code_map_lines = _code_map_lines(part) if part else []
    price_range = (part or {}).get("price_range_usd") or "—"
    material = (part or {}).get("material_grade") or "待企业确认"
    report_id = (part or {}).get("report_id") or "TR-2024-XXXX（占位）"
    brake_type = (part or {}).get("brake_type") or "—"
    return {
        "id": rec["id"],
        "brand": rec.get("brand"),
        "vehicle_raw": rec.get("vehicle_raw"),
        "vehicle_norm": rec.get("vehicle_norm"),
        "position": rec.get("position"),
        "variant": rec.get("variant"),
        "axle_key": axle_key(rec),
        "axle_label": axle_label(rec),
        "size_raw": rec.get("size_raw"),
        "l": rec.get("l"),
        "w": rec.get("w"),
        "page": rec.get("page"),
        "src": rec.get("src"),
        "cell": rec.get("cell"),
        "master_id": master_id,
        "alt_codes": alt_codes,
        "alt_codes_line": " / ".join(alt_codes) if alt_codes else "—",
        "code_map": code_map,
        "code_map_lines": code_map_lines,
        "price_range_usd": price_range,
        "price_line": f"价区（示意 USD）：{price_range}" if price_range != "—" else "价区：待核价",
        "material_grade": material,
        "report_id": report_id,
        "brake_type": brake_type,
        "spec_line": _spec_line(rec),
        "evidence_line": _evidence_line(rec),
        "confidence_line": f"置信度：{level} · {reason}",
        "pending_line": _pending_line(rec),
        "why_line": _why_line(
            rec,
            level,
            reason,
            part,
            entry=entry,
            code=code,
            vehicle=vehicle,
            possible_axle=possible_axle,
        ),
        "confidence_level": level,
        "record": rec,
        "part": part,
    }


def _dedupe_by_axle_then_size(rows: list[dict]) -> list[dict]:
    """同一 axle_key 只保留一条（跨册同桥同片）；公理 1:1。
    不按 size 跨桥去重，避免把两条不同车桥误合成一片。"""
    by_axle: dict[str, dict] = {}
    for r in rows:
        key = axle_key(r)
        if key not in by_axle:
            by_axle[key] = r
    return list(by_axle.values())


def _axle_specificity(rec: dict) -> int:
    score = 0
    if rec.get("position"):
        score += 2
    if rec.get("variant"):
        score += 1
    if rec.get("vehicle_norm"):
        score += 1
    return score


def _dedupe_by_size_prefer_specific(rows: list[dict]) -> list[dict]:
    """同尺寸只留一条：优先带前/后、A/B 的具体车桥（编码定片用）。"""
    best: dict[str, dict] = {}
    for r in rows:
        size = r.get("size_raw") or r["id"]
        cur = best.get(size)
        if cur is None or _axle_specificity(r) > _axle_specificity(cur):
            best[size] = r
    return list(best.values())


def _apply_filters(
    rows: list[dict],
    position: str | None,
    variant: str | None,
    width: float | None,
    holes: int | None,
    length: float | None = None,
) -> tuple[list[dict], int]:
    """返回过滤后列表与命中的约束个数。"""
    constraints = 0
    out = rows
    if position:
        constraints += 1
        out = [r for r in out if r.get("position") == position]
    if variant:
        constraints += 1
        out = [r for r in out if (r.get("variant") or "").upper() == variant.upper()]
    if width is not None:
        constraints += 1
        out = [r for r in out if r.get("w") is not None and abs(float(r["w"]) - width) < 0.05]
    if length is not None:
        constraints += 1
        out = [r for r in out if r.get("l") is not None and abs(float(r["l"]) - length) < 0.05]
    if holes is not None:
        constraints += 1
        out = [r for r in out if r.get("holes") == holes]
    return out, constraints


def _confidence(
    entry: str | None,
    hit_kind: str,
    n: int,
    constraint_count: int,
    unique_axle: bool,
) -> tuple[str, str]:
    """按「车桥 1:1 定片」给置信度。"""
    if n == 0:
        return "无", "未命中目录"
    if unique_axle and n == 1:
        if entry == "axle":
            return "高", "具体车桥已确认，车桥↔刹车片 1:1"
        if entry == "code":
            return "高", "现有编码已映射到唯一车桥适配"
        return "高", "已消歧到唯一车桥，车桥↔刹车片 1:1"
    if entry == "axle" and n >= 2:
        return "中", "车桥名已命中，但前/后（或 A/B）未定——每一位置各对应一片，请确认"
    if entry == "vehicle":
        if n <= 4:
            return "中", "仅车型：列出可能车桥适配，须确认具体车桥后才能 1:1 定片"
        return "低", "该车型可能车桥偏多，请提供车桥型号或前/后"
    if hit_kind in ("exact", "alias", "code") and constraint_count >= 1 and n <= 2:
        return "高", "约束后接近唯一车桥"
    if n <= 4:
        return "中", "待确认具体车桥（含前/后）"
    return "低", "候选偏多，请补充车桥"


def match(
    q: str = "",
    position: str | None = None,
    width: float | None = None,
    holes: int | None = None,
    length: float | None = None,
    variant: str | None = None,
    axle: str | None = None,
    code: str | None = None,
    year: str | None = None,
    brake_type: str | None = None,
    tonnage: str | None = None,
) -> dict:
    """三入口：车型 q / 车桥 axle / 现有编码 code。可组合，编码优先。
    五问辅字段：year / brake_type / tonnage（软过滤，不臆造）。"""
    records = load_records()
    q = (q or "").strip()
    axle = (axle or "").strip() or None
    code = (code or "").strip() or None
    year = (year or "").strip() or None
    brake_type = (brake_type or "").strip() or None
    tonnage = (tonnage or "").strip() or None

    norm_q, pos_from_q, var_from_q = normalize_query(q) if q else ("", None, None)
    position = position or pos_from_q
    variant = variant or var_from_q

    # 吨位关键字可并入车桥检索（如 35T → 汉德桥35T）
    axle_search = axle
    if tonnage and axle_search and tonnage.upper() not in axle_search.upper():
        axle_search = f"{axle_search}{tonnage}"
    elif tonnage and not axle_search and not code:
        # 仅吨位时尝试当车桥片段
        axle_search = tonnage

    weak_q = (not norm_q) or norm_q in ("一台卡车要刹车片", "卡车刹车片", "刹车片")
    if weak_q and not axle and not code and not tonnage:
        return {
            "q": q,
            "norm_q": norm_q,
            "axle": axle,
            "code": code,
            "year": year,
            "brake_type": brake_type,
            "tonnage": tonnage,
            "hit_kind": "none",
            "candidates": [],
            "nearest": nearest_norms(norm_q or "天锦", 3),
            "confidence_level": "无",
            "assert_unique": False,
            "position": position,
            "width": width,
            "holes": holes,
            "followups": _followups(None, position, width, ask_five=True),
            "miss": True,
            "miss_reason": "信息不足 · 请先问车桥",
            "entry": None,
            "hit_note": None,
            "step": 1,
            "poster": True,
        }

    hit_kind = "none"
    pool: list[dict] = []
    hit_note = None
    entry = None  # vehicle | axle | code

    # —— 入口优先级：编码 > 车桥 > 车型 ——
    if code:
        pool, hit_note = _recall_by_code(code, records)
        if pool:
            hit_kind = "code"
            entry = "code"

    if not pool and axle_search:
        pool = _recall_by_axle(axle_search, records)
        if pool:
            hit_kind = "axle"
            entry = "axle"
            hit_note = f"车桥关键字「{axle or axle_search}」命中目录车型名"

    if not pool and not weak_q:
        exact = [r for r in records if r.get("vehicle_norm") == norm_q and r.get("page")]
        if exact:
            hit_kind = "exact"
            entry = "vehicle"
            pool = _expand_pool(records, [norm_q])
            hit_note = f"车型精确命中 {norm_q}"
        else:
            targets = _alias_targets(norm_q)
            if targets:
                hit_kind = "alias"
                entry = "vehicle"
                pool = _expand_pool(records, targets)
                hit_note = f"车型别名命中 → {', '.join(targets)}"

    # 车型 + 车桥同时给：在车型池上再按车桥缩一次
    if pool and axle_search and entry == "vehicle":
        axle_ids = {r["id"] for r in _recall_by_axle(axle_search, records)}
        narrowed = [r for r in pool if r["id"] in axle_ids]
        if narrowed:
            pool = narrowed
            hit_note = (hit_note or "") + f"；再按车桥「{axle or axle_search}」收缩"

    if not pool:
        tip = norm_q or (normalize_query(axle or "")[0] if axle else "") or (code or "车型")
        return {
            "q": q,
            "norm_q": norm_q,
            "axle": axle,
            "code": code,
            "year": year,
            "brake_type": brake_type,
            "tonnage": tonnage,
            "hit_kind": "none",
            "candidates": [],
            "nearest": nearest_norms(tip, 3) if not code else [],
            "confidence_level": "无",
            "assert_unique": False,
            "position": position,
            "width": width,
            "holes": holes,
            "followups": _followups(None, position, width, ask_five=True),
            "miss": True,
            "miss_reason": "未收录",
            "entry": entry,
            "hit_note": None,
            "step": 1,
            "poster": True,
        }

    filtered, c_count = _apply_filters(pool, position, variant, width, holes, length)
    if hit_kind == "code":
        c_count = max(c_count, 1)

    filtered = [r for r in filtered if r.get("page") and r.get("src")]
    if entry == "code":
        # 编码映射到尺寸/条目：同一尺寸只留最具体的一条车桥（有前/后、A/B 优先）
        filtered = _dedupe_by_size_prefer_specific(filtered)
    else:
        filtered = _dedupe_by_axle_then_size(filtered)

    # 鼓/盘软过滤（只在 parts_master 有标注时生效）
    if brake_type and filtered:
        typed = []
        for r in filtered:
            p = _part_for_record(r)
            bt = (p or {}).get("brake_type") or ""
            if not bt or brake_type in bt:
                typed.append(r)
        if typed:
            filtered = typed
            c_count = max(c_count, 1)

    # 唯一车桥：只剩 1 个 axle_key（去重后 1 条）
    unique_axle = len(filtered) == 1
    # 车型入口默认不能唯一，除非消歧到 1 条
    if entry == "vehicle" and unique_axle and c_count >= 1:
        pass  # 已用尺寸/前后消歧到唯一桥
    elif entry == "vehicle" and not unique_axle:
        unique_axle = False

    level, reason = _confidence(entry, hit_kind, len(filtered), c_count, unique_axle)
    if hit_note:
        reason = f"{hit_note}；{reason}"

    # 公理：仅当定位到唯一具体车桥时允许「建议规格」
    assert_unique = unique_axle and len(filtered) == 1 and level == "高"
    # 编码或「车桥+位置」命中单条时强制高+唯一
    if len(filtered) == 1 and entry in ("code", "axle") and (entry == "code" or position or filtered[0].get("position") is None):
        # axle 单条：要么带了 position 过滤，要么目录本身无前后之分
        if entry == "code" or position or not _axle_needs_position_choice(filtered):
            level = "高"
            reason = (hit_note + "；" if hit_note else "") + "具体车桥已确认，车桥↔刹车片 1:1"
            assert_unique = True
    if entry == "axle" and position and len(filtered) == 1:
        level = "高"
        reason = (hit_note + "；" if hit_note else "") + f"车桥「{axle}」+ {position}桥 1:1 定片"
        assert_unique = True

    # 海报：当场给出 Top 2–3；车型-only 为「可能车桥」最多展示 4（验收 V1）
    limit = 4 if entry == "vehicle" and not assert_unique else 3
    shown = filtered[:limit]
    cards = [
        card_from_record(
            r,
            level,
            reason,
            entry=entry,
            code=code,
            vehicle=q,
            possible_axle=(entry == "vehicle" and not assert_unique),
        )
        for r in shown
    ]

    # 步骤：1 先问车桥 / 2–3 已出候选
    if entry == "vehicle" and not assert_unique:
        step = 1
    elif assert_unique or entry in ("code", "axle"):
        step = 3
    else:
        step = 2

    return {
        "q": q,
        "norm_q": norm_q,
        "axle": axle,
        "code": code,
        "year": year,
        "brake_type": brake_type,
        "tonnage": tonnage,
        "hit_kind": hit_kind,
        "candidates": cards,
        "candidate_total": len(filtered),
        "nearest": [],
        "confidence_level": level,
        "assert_unique": assert_unique,
        "position": position,
        "width": width,
        "holes": holes,
        "followups": _followups(
            filtered,
            position,
            width,
            entry=entry,
            ask_five=(entry == "vehicle" and not assert_unique),
            year=year,
            brake_type=brake_type,
            tonnage=tonnage,
        ),
        "miss": False,
        "miss_reason": None,
        "constraint_count": c_count,
        "entry": entry,
        "hit_note": hit_note,
        "rule": "axle_1to1_poster",
        "step": step,
        "poster": True,
    }


def _axle_needs_position_choice(rows: list[dict]) -> bool:
    positions = {r.get("position") for r in rows if r.get("position")}
    return len(positions) > 1


def _followups(
    rows: list[dict] | None,
    position: str | None,
    width: float | None,
    entry: str | None = None,
    ask_five: bool = False,
    year: str | None = None,
    brake_type: str | None = None,
    tonnage: str | None = None,
) -> dict:
    """中英追问：优先要「具体车桥」（海报五问）。"""
    need_pos = not position
    zh_parts = []
    en_parts = []
    if rows is None or ask_five:
        missing = []
        if not year:
            missing.append("年份")
        if not brake_type:
            missing.append("鼓或盘")
        if not tonnage:
            missing.append("吨位")
        zh_parts = [
            "先问车桥，不问车型：请提供车桥型号（一片对应一条具体车桥）。",
            "五问补全：" + ("、".join(missing) if missing else "已填辅项") + "；并确认前/后，有旧件可发参考照片。",
            "若只报车型：说明是否换过车桥，或发旧件尺寸 / 现有编码。",
        ]
        en_parts = [
            "Ask axle first: provide the exact axle model (one axle ↔ one lining).",
            "Five prompts: year / drum-or-disc / tonnage / front-or-rear / photo of old part.",
            "Vehicle-only: say if the axle was replaced, or send size / aftermarket code.",
        ]
        if rows and entry == "vehicle":
            labels = [axle_label(r) for r in rows[:4]]
            zh_parts.insert(1, "当前仅知车型，可能车桥：" + "；".join(labels) + "——请勾选或补车桥后再定件号。")
            en_parts.insert(1, "Vehicle-only; possible axles: " + "; ".join(labels))
    else:
        zh_parts.append("规则：一条具体车桥对应一片刹车片。请确认下方哪一条车桥。")
        en_parts.append("Rule: one specific axle maps to one lining. Please confirm which axle below.")
        positions = {r.get("position") for r in rows if r.get("position")}
        if need_pos and len(positions) > 1:
            zh_parts.append("请选择前桥或后桥（选定后即可 1:1 定片）。")
            en_parts.append("Please choose front or rear axle to lock the unique lining.")
        variants = {r.get("variant") for r in rows if r.get("variant")}
        if len(variants) > 1:
            zh_parts.append("存在 A/B 两种车桥版本，请确认版本或提供旧件长度/编码。")
            en_parts.append("There are A/B axle variants — confirm the variant or send length/code of the old part.")
        if entry == "vehicle" and len(rows) > 1:
            labels = []
            for r in rows[:4]:
                labels.append(axle_label(r))
            zh_parts.append("当前仅知车型，可能车桥：" + "；".join(labels))
            en_parts.append("Vehicle-only query; possible axles: " + "; ".join(labels))
        if not zh_parts:
            zh_parts.append("如需报价，请补充数量、目的港与认证要求（如 ECE R90）。")
            en_parts.append("For a formal quote, please share qty, destination port, and any certs (e.g. ECE R90).")

    return {
        "zh": "\n".join(f"{i}. {t}" for i, t in enumerate(zh_parts[:4], 1)),
        "en": "\n".join(f"{i}. {t}" for i, t in enumerate(en_parts[:4], 1)),
    }
