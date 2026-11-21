import re

BRAKE = re.compile(r"\bbrakes?\b|刹车|制动|freno|freio", re.I)
COMMERCIAL = re.compile(r"\b(?:truck\w*|trailer\w*|heavy[ -]?duty|commercial vehicle\w*|t850)\b|重卡|商用车|卡车", re.I)
UNRELATED = re.compile(r"passenger[ -]?car|floor mats?|seat covers?|wiper blades?|手机壳|乘用车|脚垫|座套|雨刷", re.I)
OTHER_PART = re.compile(r"floor mats?|seat covers?|wiper blades?|手机壳|脚垫|座套|雨刷", re.I)
B2B = re.compile(r"\b(?:we (?:distribute|wholesale|import|supply|operate)|(?:distributor|wholesaler|importer|fleet buyer)\b)|"
                 r"(?:我们|我司|本公司).{0,8}(?:经销|批发|进口|车队|采购)|分销商|经销商|批发商|进口商", re.I)
SELF = re.compile(r"\b(?:we|our company|our fleet|i work for)\b|我们|我司|本公司", re.I)
ORDER = re.compile(r"想订|批量采购|订购|采购\s*\d|\b(?:want to (?:buy|order)|place an order|buy in bulk)\b", re.I)
NEGATION = re.compile(r"\b(?:not interested|no longer interested|do not need|don't need|cancel (?:the|our) order)\b|不感兴趣|不需要|取消(?:订单|询价)", re.I)
SENSITIVE = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}|https?://|(?:\+?\d[\s().-]*){8,}|加微信|手机号|银行卡")

BUYER_DEFAULTS = {"product": 30, "procurement": 30, "china": 15, "show": 15, "contact": 10}
BUYER_DIMENSIONS = {"product": "产品匹配", "procurement": "采购或进口证据", "china": "中国采购",
                    "show": "展会意向", "contact": "可联系"}
BUYER_BANDS = {"hot": 90, "priority": 70, "review": 40}


def part(label, max_points, points, evidence=""):
    return {"label": label, "max": max_points, "points": points, "evidence": evidence}


def text_match(pattern, text):
    result = pattern.search(text)
    return result.group() if result else ""


def summarize(parts, cap=100, note=""):
    raw = sum(item["points"] for item in parts)
    total = min(raw, cap)
    return {"total": total, "raw": raw, "parts": parts, "note": note,
            "tier": "high" if total >= 70 else "medium" if total >= 35 else "low"}


def comment_score(body, fields, purchase="", consultation=""):
    purchase = purchase or text_match(ORDER, body)
    commercial = text_match(COMMERCIAL, body)
    generic = text_match(BRAKE, body)
    product = fields.get("product", "")
    business = text_match(B2B, body)
    declared = text_match(SELF, body)
    specifics = []
    if fields.get("code"):
        specifics.append(fields["code"])
    if fields.get("quantity"):
        specifics.append(fields["quantity"] + " " + fields.get("unit", ""))
    elif fields.get("width_mm"):
        specifics.append(fields["width_mm"] + " mm")
    elif fields.get("holes"):
        specifics.append(fields["holes"] + " 孔")
    product_points = 25 if product and commercial else 14 if product else 8 if generic else 0
    breakdown = [
        part("采购行为", 30, 30 if purchase else 16 if consultation else 0, purchase or consultation),
        part("产品匹配", 25, product_points, " · ".join(x for x in (product or generic, commercial) if x)),
        part("B2B 自述", 25, 25 if business else 0, business),
        part("具体询价信息", 15, 15 if len(specifics) >= 2 else 7 if specifics else 0, " · ".join(specifics)),
        part("企业自述", 5, 5 if declared else 0, declared),
    ]
    if NEGATION.search(body):
        return summarize(breakdown, 10, "留言表达了取消或无采购意向，优先级上限 10 分。")
    if UNRELATED.search(body) and (not (commercial and product) or OTHER_PART.search(body)):
        return summarize(breakdown, 10, "出现非目标品类，优先级上限 10 分。")
    if not generic and not product:
        return summarize(breakdown, 34, "缺少制动件产品信号，优先级上限 34 分。")
    if not purchase:
        return summarize(breakdown, 69, "目前为一般咨询，等到明确询价、起订量或订购信号后再转为高意向。")
    return summarize(breakdown)


def buyer_score(axes, email, weights=BUYER_DEFAULTS):
    def points(key, axis=None):
        item = axes.get(axis or key)
        supported = item and item["state"] in {"supported", "declared"}
        maximum = weights[key]
        return part(BUYER_DIMENSIONS[key], maximum, maximum if supported else 0, item["quote"] if supported else "")

    show = points("show", "attendance")
    if not show["evidence"]:
        travel = axes.get("travel")
        if travel and travel["state"] in {"supported", "declared"}:
            show = part(BUYER_DIMENSIONS["show"], weights["show"], weights["show"] // 2, travel["quote"])
    parts = [
        *(points(key) for key in ("product", "procurement", "china")),
        show,
        part(BUYER_DIMENSIONS["contact"], weights["contact"], weights["contact"] if email else 0, "已填写邮箱" if email else ""),
    ]
    if axes.get("product") and axes["product"]["state"] == "excluded":
        return {**summarize(parts, 34, "已有品类不符的证据。"), "tier": "defer"}
    qualified = all(axes.get(key) and axes[key]["state"] == "supported" for key in ("product", "procurement"))
    result = summarize(parts) if qualified else summarize(parts, 69, "产品与采购证据齐全后才能进入前两档。")
    result["tier"] = next((tier for tier, minimum in BUYER_BANDS.items() if result["total"] >= minimum), "nurture")
    return result
