"""渐进式追问：一次一问；能定片就停；每问可点「不知道」或「返回」。"""

from __future__ import annotations


def decide_next(
    *,
    q: str,
    axle: str,
    code: str,
    position: str | None,
    length: float | None,
    result: dict | None,
    skip_code: bool = False,
    skip_axle: bool = False,
) -> dict:
    """根据当前答案与匹配结果，决定下一问或收工。"""
    answered = _answered(q, axle, code, position, length, skip_code, skip_axle)

    # —— 第 1 问：编码（可跳过）——
    if not code and not skip_code and not axle and not q:
        return ask_code_first(answered)

    # —— 第 2 问：车桥（编码已跳过或未命中后）——
    if not axle and not skip_axle and not q and (skip_code or not code):
        if not code:
            return ask_axle(answered, skip_code=skip_code)

    if result is None and not q and not axle and not code:
        if skip_code and not skip_axle:
            return ask_axle(answered, skip_code=True)
        if skip_code and skip_axle:
            return ask_vehicle_first(answered)
        return ask_code_first(answered)

    if result is None:
        if not axle and not skip_axle:
            return ask_axle(answered, skip_code=skip_code or not code)
        if not q:
            return ask_vehicle_first(answered)
        return _ask_fallback(answered)

    if result.get("miss"):
        if code and not axle and not skip_axle:
            return {
                "phase": "ask",
                "key": "axle",
                "title": "这个编码未收录，车桥型号是什么？",
                "hint": "编码对不上时改问车桥。也可点「不知道」改报车型。",
                "input": "text",
                "placeholder": "如 汉德桥35T",
                "name": "axle",
                "skip": {
                    "label": "不知道，下一步（改问车型）",
                    "params": {
                        "code": code,
                        "skip_axle": "1",
                        "wiz": "vehicle",
                    },
                },
                "back": {
                    "label": "← 返回上一步",
                    "params": {},  # 回第一问
                    "href": "/fitment",
                },
                "demos": [
                    {"label": "汉德桥35T", "params": {"code": code, "axle": "汉德桥35T"}},
                ],
                "answered": answered,
                "done": False,
            }
        return {
            "phase": "miss",
            "key": None,
            "title": "未收录",
            "hint": result.get("miss_reason") or "目录未命中",
            "answered": answered,
            "done": False,
            "nearest": result.get("nearest") or [],
            "back": _back_from_miss(q, axle, code, skip_code, skip_axle),
        }

    if result.get("assert_unique") and result.get("candidates"):
        return {
            "phase": "done",
            "key": None,
            "title": "已精确定片",
            "hint": "可生成报价依据。",
            "answered": answered,
            "done": True,
            "show_candidates": True,
            "back": _back_from_result(q, axle, code, position, length, skip_code),
        }

    cands = result.get("candidates") or []
    entry = result.get("entry")

    if entry == "vehicle" and not axle:
        # 已走到车型 = 车桥已知晓或已跳过，不再追问车桥；直接列出该车型全部可能适配
        return {
            "phase": "pick",
            "key": "q",
            "title": f"「{q or '该车型'}」的可能适配",
            "hint": "不知道具体车桥时，下列每条是一种可能车桥↔片（1:1）。请勾选最接近的一条；不能对外断言「就是这款」。",
            "answered": answered,
            "done": False,
            "show_candidates": True,
            "back": {
                "label": "← 返回上一步",
                "params": {"skip_code": "1", "skip_axle": "1", "wiz": "vehicle"},
            },
        }

    positions = {c.get("position") for c in cands if c.get("position")}
    if not position and len(positions) > 1:
        return {
            "phase": "ask",
            "key": "position",
            "title": "前桥还是后桥？",
            "hint": f"「{axle or q}」前、后各对应一片，选一个即可定型。",
            "input": "choice",
            "name": "position",
            "choices": [
                {
                    "label": "前桥",
                    "params": _carry(q, axle, code, length, position="前"),
                },
                {
                    "label": "后桥",
                    "params": _carry(q, axle, code, length, position="后"),
                },
            ],
            "skip": {
                "label": "不知道，先看前/后两条候选",
                "params": {**_carry(q, axle, code, length), "force_pick": "1"},
            },
            "back": {
                "label": "← 返回上一步",
                "params": _back_params_before_axle_result(q, axle, code, skip_code),
            },
            "answered": answered,
            "done": False,
            "show_preview": True,
        }

    variants = {c.get("variant") for c in cands if c.get("variant")}
    if length is None and len(cands) > 1 and (len(variants) > 1 or len(cands) > 1):
        length_choices = []
        for c in cands:
            L = c.get("l")
            if L is None:
                continue
            length_choices.append(
                {
                    "label": f"{c.get('variant') or ''}版 · 长 {L:g} · {c.get('size_raw')}".strip(
                        " ·"
                    ),
                    "params": _carry(
                        q, axle, code, L, position=position or c.get("position")
                    ),
                }
            )
        back_p = _carry(q, axle, code, None, position=None)
        if skip_code:
            back_p["skip_code"] = "1"
        # 返回到「前/后」那一步：保留车桥，去掉 length/position 让系统再问位置
        # 若已有 position，返回应清 length 并保留 position 的上一问=position 问
        # 简化：返回 = 保留 axle/q/code，去掉 length 与 position → 再问前/后
        return {
            "phase": "ask",
            "key": "length",
            "title": "哪个版本 / 旧件多长？",
            "hint": "还有 A/B 或多种尺寸。不知道就点选一条或先看候选。",
            "input": "text",
            "placeholder": "如 188",
            "name": "length",
            "choices": length_choices[:4],
            "skip": {
                "label": "不知道，先看候选再勾选",
                "params": {
                    **_carry(q, axle, code, None, position=position),
                    "force_pick": "1",
                },
            },
            "back": {
                "label": "← 返回上一步",
                "params": _back_to_position_step(q, axle, code, skip_code),
            },
            "answered": answered,
            "done": False,
            "show_preview": True,
        }

    return {
        "phase": "pick",
        "key": None,
        "title": "请勾选最接近的一条",
        "hint": "信息已尽量收窄，请确认后生成报价依据。",
        "answered": answered,
        "done": False,
        "show_candidates": True,
        "back": _back_from_result(q, axle, code, position, length, skip_code),
    }


def ask_code_first(answered: list | None = None) -> dict:
    return {
        "phase": "ask",
        "key": "code",
        "title": "客户有现有编码吗？",
        "hint": "有码最快：一物多码可跨厂/同企多码同片。没有就点「不知道」。",
        "input": "text",
        "placeholder": "如 DF-PAD-188A / WVA-TJ-KR-FA",
        "name": "code",
        "skip": {
            "label": "不知道，下一步",
            "params": {"skip_code": "1", "wiz": "axle"},
        },
        "back": {
            "label": "← 返回首页",
            "href": "/",
            "params": None,
        },
        "demos": [
            {"label": "东风渠道A", "params": {"code": "DF-PAD-188A"}},
            {"label": "东风渠道B·同片", "params": {"code": "DF-PAD-188B"}},
            {"label": "后市场WVA", "params": {"code": "WVA-TJ-KR-FA"}},
        ],
        "answered": answered or [],
        "done": False,
    }


def ask_axle(answered: list | None = None, skip_code: bool = False) -> dict:
    params_skip = {"skip_code": "1", "skip_axle": "1", "wiz": "vehicle"}
    if not skip_code:
        params_skip = {"skip_axle": "1", "wiz": "vehicle"}
    return {
        "phase": "ask",
        "key": "axle",
        "title": "车桥型号是什么？",
        "hint": "一片对应一条具体车桥。不知道车桥可点「不知道」改问车型。",
        "input": "text",
        "placeholder": "如 汉德桥35T",
        "name": "axle",
        "skip": {
            "label": "不知道，下一步",
            "params": params_skip,
        },
        "back": {
            "label": "← 返回上一步",
            "href": "/fitment",
            "params": {},
        },
        "demos": [
            {"label": "汉德桥35T", "params": {"axle": "汉德桥35T", "skip_code": "1"}},
        ],
        "answered": answered or [],
        "done": False,
    }


def ask_vehicle_first(answered: list | None = None) -> dict:
    return {
        "phase": "ask",
        "key": "q",
        "title": "客户报的车型是？",
        "hint": "不知道车桥时填车型即可，下一步会直接列出该车型全部可能适配供勾选。",
        "input": "text",
        "placeholder": "如 天锦KR",
        "name": "q",
        "skip": {
            "label": "编码、车桥、车型都不知道",
            "params": {
                "skip_code": "1",
                "skip_axle": "1",
                "skip_vehicle": "1",
                "wiz": "stuck",
            },
        },
        "back": {
            "label": "← 返回上一步",
            "params": {"skip_code": "1", "wiz": "axle"},
        },
        "demos": [
            {"label": "天锦KR", "params": {"q": "天锦KR", "skip_code": "1"}},
            {"label": "杰狮C6", "params": {"q": "杰狮C6", "skip_code": "1"}},
        ],
        "answered": answered or [],
        "done": False,
    }


def _back_params_before_axle_result(
    q: str, axle: str, code: str, skip_code: bool
) -> dict:
    """从前/后问回到「问车桥」或「只有车型」。"""
    if q and axle:
        return {"q": q, "skip_code": "1"}
    if axle and (skip_code or not code):
        return {"skip_code": "1", "wiz": "axle"}
    if code:
        return {}
    return {"skip_code": "1", "wiz": "axle"}


def _back_to_position_step(
    q: str, axle: str, code: str, skip_code: bool
) -> dict:
    """从长度/版本问回到前/后问：保留车桥，去掉 position/length。"""
    p = _carry(q, axle, code, None)
    if skip_code:
        p["skip_code"] = "1"
    return p


def _back_from_result(
    q: str,
    axle: str,
    code: str,
    position: str | None,
    length: float | None,
    skip_code: bool,
) -> dict:
    if length is not None and position:
        p = _carry(q, axle, code, None, position=position)
        if skip_code:
            p["skip_code"] = "1"
        return {"label": "← 返回上一步", "params": p}
    if position:
        return {
            "label": "← 返回上一步",
            "params": _back_to_position_step(q, axle, code, skip_code),
        }
    if axle:
        return {
            "label": "← 返回上一步",
            "params": _back_params_before_axle_result(q, axle, code, skip_code),
        }
    if code:
        return {"label": "← 返回上一步", "href": "/fitment", "params": {}}
    if q:
        return {
            "label": "← 返回上一步",
            "params": {"skip_code": "1", "wiz": "vehicle"},
        }
    return {"label": "← 返回上一步", "href": "/fitment", "params": {}}


def _back_from_miss(
    q: str, axle: str, code: str, skip_code: bool, skip_axle: bool
) -> dict:
    if code and not axle:
        return {"label": "← 返回上一步", "href": "/fitment", "params": {}}
    if axle:
        return {
            "label": "← 返回上一步",
            "params": _back_params_before_axle_result(q, axle, code, skip_code),
        }
    if q:
        return {
            "label": "← 返回上一步",
            "params": {"skip_code": "1", "wiz": "vehicle"},
        }
    return {"label": "← 返回上一步", "href": "/fitment", "params": {}}


def _answered(
    q: str,
    axle: str,
    code: str,
    position: str | None,
    length: float | None,
    skip_code: bool,
    skip_axle: bool,
) -> list:
    answered = []
    if code:
        answered.append(("编码", code))
    elif skip_code:
        answered.append(("编码", "不知道"))
    if axle:
        answered.append(("车桥", axle))
    elif skip_axle:
        answered.append(("车桥", "不知道"))
    if q:
        answered.append(("车型", q))
    if position:
        answered.append(("位置", position + "桥"))
    if length is not None:
        answered.append(("长度", f"{length:g} mm"))
    return answered


def _carry(
    q: str,
    axle: str,
    code: str,
    length: float | None,
    position: str | None = None,
) -> dict:
    params: dict = {}
    if q:
        params["q"] = q
    if axle:
        params["axle"] = axle
    if code:
        params["code"] = code
    if position:
        params["position"] = position
    if length is not None:
        params["length"] = f"{length:g}"
    return params


def _ask_fallback(answered: list) -> dict:
    return {
        "phase": "ask",
        "key": "axle",
        "title": "请补充车桥型号",
        "hint": "信息不足，先问车桥。",
        "input": "text",
        "name": "axle",
        "placeholder": "车桥型号",
        "skip": {
            "label": "不知道，下一步",
            "params": {"skip_axle": "1", "wiz": "vehicle"},
        },
        "back": {
            "label": "← 返回上一步",
            "href": "/fitment",
            "params": {},
        },
        "answered": answered,
        "done": False,
    }
