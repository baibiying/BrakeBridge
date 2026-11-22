"""Conservative first-response policy for public comments."""

import re

from catalog import find_candidates
from scoring import NEGATION, OTHER_PART, SENSITIVE

NEGATIVE = re.compile(r"\b(?:not interested|do not contact|don't contact|stop messaging|no longer need|no price needed)\b|不感兴趣|不要联系|不用报价|不采购|取消询价|不需要这批", re.I)
SUPPORT = re.compile(r"\b(?:refund|wrong parts?|damaged|failed|failure|safety issue|complaint|defect\w*)\b|投诉|退款|损坏|刹车失灵|错发|售后(?!市场|渠道|配件)", re.I)
OTHER_LANGUAGE = re.compile(r"\b(?:cotiz\w*|presupuesto|orçamento|preço|bonjour|devis|juegos)\b", re.I)
ENGAGEMENT = re.compile(r"\b(?:great|nice|awesome|thanks)\b|点赞|不错|谢谢分享", re.I)
EXPO = re.compile(r"展位|展会|汽配展|法兰克福|automechanika|\b(?:booth|trade show|exhibition)\b", re.I)
EXPO_VISIT = re.compile(r"参观.{0,4}工厂|验厂|factory (?:visit|tour)|visit (?:your|the) factory", re.I)
EXPO_MEETING = re.compile(r"约见|见面|预约|碰面|\b(?:meet|meeting|appointment)\b", re.I)
SAMPLES = re.compile(r"样品|样件寄|\bsamples?\b", re.I)
CATALOG_REQUEST = re.compile(r"目录|\bcatalog(?:ue)?\b", re.I)
PART_REFERENCE = re.compile(r"(?<![A-Z0-9])(?:新\s*|new\s*)?T850(?![A-Z0-9])|\b(?:WVA|FMSI|OE)\s*[:#-]?\s*\d+", re.I)
PRICE_REQUEST = re.compile(r"多少钱|什么价|价格|怎么卖|报价|报个价|询个价|多钱|单价|\b(?:prices?|pricing|how much|quotations?|quote)\b", re.I)


def exhibition_reply(body, zh, event):
    if event and event.get("name") and event.get("booth"):
        if zh:
            when = "，时间是" + event["date"] if event.get("date") else ""
            where = "，地点是" + event["venue"] if event.get("venue") else ""
            opening = f"您好，冠英将参加{event['name']}{when}{where}，展位号 {event['booth']}。"
        else:
            details = ", ".join(event[key] for key in ("date", "venue") if event.get(key))
            details = f" ({details})" if details else ""
            opening = f"Yes, Kooying will be at {event['name']}{details}, booth {event['booth']}."
    else:
        opening = ("您好，您问的是参展和展位号。请问具体是哪一场展会？我们核对后会把展位信息发给您。"
                   if zh else "Which trade show are you planning to attend? We can check our participation and share the booth details.")
    if EXPO_VISIT.search(body):
        return opening + ("如您想参观工厂，请通过官方渠道发一下到访日期和人数，我们核对接待安排。"
                          if zh else "If you would like to visit our factory, please send your preferred date and group size privately.")
    if SAMPLES.search(body):
        return opening + ("方便说一下想看的零件号或品类吗？我们核对可安排的展品。"
                          if zh else "Which part number or product type would you like to see? We will check the samples available.")
    if event and event.get("name") and event.get("booth"):
        return opening + ("您预计哪天到展？如果方便，我们可以提前约个时间在展位见面。"
                          if zh else "Which day do you plan to attend? We can arrange a time to meet at the booth.")
    return opening


def plan(body, analysis, source, data_kind, event=None):
    fields = analysis["fields"]
    extraction = analysis["extraction"]
    result = {"mode": "manual", "reason": "采购意图或语言待确认", "draft": "",
              "candidate": None, "extraction": extraction,
              "action": "核对原记录和客户上下文，再选择跟进方式。"}
    # Declining a sample is compatible with requesting a price for a real order.
    purchase_context = re.sub(r"不需要(?:样品|样件|寄样)|(?:do not|don't) need (?:a |any )?samples?\b", "", body, flags=re.I) if PRICE_REQUEST.search(body) else body
    if NEGATIVE.search(body) or NEGATION.search(purchase_context):
        return {**result, "mode": "skip", "reason": "对方明确表示无需跟进", "action": "留档，结束这条线索的外联。"}
    if SENSITIVE.search(body):
        return {**result, "reason": "含敏感信息，交销售私下核对"}
    zh = analysis["language"] == "中文"
    public = source in {"facebook", "instagram", "linkedin", "youtube", "social_demo"} or source.startswith("figeon:")
    is_support = bool(SUPPORT.search(body))
    if OTHER_PART.search(body) and not fields.get("product") and not is_support:
        return {**result, "mode": "skip", "reason": "所询品类与冠英产品不匹配", "action": "按无关品类留档。"}
    qualified = analysis["intent"] in {"询价", "展会约见", "展会咨询", "产品咨询"} or any(
        pattern.search(body) for pattern in (EXPO, EXPO_VISIT, SAMPLES, CATALOG_REQUEST)
    ) or is_support or PRICE_REQUEST.search(body)
    if not qualified:
        if analysis["intent"] == "普通互动" or ENGAGEMENT.search(body) or not any(c.isalnum() for c in body):
            return {**result, "mode": "skip", "reason": "普通互动，无需跟进", "action": "保留互动记录。"}
        if public and re.fullmatch(r"(?:这个呢|这个有吗|有吗|这款呢|你好|您好|can you help|hello|hi)[？?！!.。\s]*", body.strip(), re.I):
            return {**result, "reason": "对象或需求不明确",
                    "draft": ("您好，您想了解哪款产品？可以发零件号或标明图片里的位置，我们先帮您查。"
                              if zh else "Which part are you asking about? You can share a part number or point to the item in the post."),
                    "action": "先打开原帖核对上下文，确认产品或需求后从原渠道回复。"}
        return result
    if not public:
        if source != "inquiry":
            return {**result, "reason": "由原邮件或私信线程跟进"}
        result["action"] = "打开已有询盘，在原会话中回复，处理结果记录在此处。"
    if not zh and OTHER_LANGUAGE.search(body):
        return {**result, "reason": "疑似非中英文询盘，交销售核对语言与需求"}
    draft = ("您好，感谢留言。我们已收到，会安排同事核对。欢迎通过冠英官方联系渠道补充信息，请勿在公开区留下个人联系方式。"
             if zh else "Thank you for your message. Our team will review it. Please use Kooying’s official contact channels to share any additional details privately.")
    if is_support:
        if re.search(r"刹车失灵|制动失灵|\b(?:brake failure|brakes? failed|safety issue)\b", body, re.I):
            draft = ("请先停止使用疑似故障部件，并通过冠英官方渠道联系售后核查。"
                     if zh else "Please stop using the suspected faulty part and contact Kooying support through an official channel.")
        return {**result, "mode": "acknowledge", "reason": "售后或质量问题，转人工核查", "draft": draft,
                "action": "交售后核查订单、批次和问题照片，通过私下渠道收集资料。"}
    if analysis["intent"].startswith("展会") or EXPO.search(body) or EXPO_VISIT.search(body):
        return {**result, "mode": "acknowledge", "reason": "客户咨询展会，先答参展或展位，再确认到展安排",
                "draft": exhibition_reply(body, zh, event),
                "action": "请确认双方空档，再记录会面时间和地点。" if EXPO_MEETING.search(body)
                else "这是参展线索。先确认到展日或参观需求，再安排会面。"}
    # The catalog match is a lead for sales verification, never a confirmed fit or price.
    required = ("code", "position", "width_mm", "holes")
    if all(fields.get(key) for key in required):
        positions = re.findall(r"\b(?:front|rear)\b|前刹|后刹", body, re.I)
        widths = re.findall(r"(\d+(?:\.\d+)?)\s*(?:mm|毫米)", body, re.I)
        holes = re.findall(r"(\d+)\s*(?:holes?\b|孔)", body, re.I)
        references = {re.sub(r"\s+", "", value.casefold()) for value in PART_REFERENCE.findall(body)}
        candidates, _ = find_candidates(fields)
        if len(candidates) == 1 and len(positions) == len(widths) == len(holes) == len(references) == 1:
            candidate = candidates[0]
            reference = candidate["source_name"] if zh else (
                f'{candidate["series"].replace("新", "New ")} {candidate["position"]}, '
                f'{candidate["width_mm"]} mm wide, {candidate["holes"]}-hole brake lining'
            )
            draft = (f"按您提供的参数，目录候选是{reference}。请通过冠英官方联系渠道提供车桥型号或旧件编号，由销售核实适配后报价。"
                     if zh else f"The catalog candidate is {reference}. Please contact Kooying privately with the axle model or old part number so our team can verify fitment before quoting.")
            if data_kind != "synthetic":
                draft = ("您好，您给出的规格已找到目录候选。请通过官方联系渠道发来旧件编号或车桥型号，销售核实后会跟进。"
                         if zh else "We found a possible catalog match. Please use our official contact channel to share the old part number or axle model so sales can verify it.")
            if extraction["quantity_note"]:
                draft += " 也请确认实际采购数量。" if zh else " Please also confirm the order quantity."
            return {**result, "mode": "candidate", "reason": "编号、位置、宽度和孔数唯一命中目录",
                    "draft": draft, "candidate": candidate,
                    "action": "进入报价工作台。核对车桥或旧件及材质，再填写正式单价。"}
    if extraction["quantity_note"]:
        return {**result, "mode": "acknowledge", "reason": "采购数量有歧义，先确认原文",
                "draft": ("您好，请确认您实际需要的数量及单位，并通过冠英官方联系渠道补充旧件编号。"
                          if zh else "Could you confirm your order quantity and unit, and share the old part number through Kooying’s official contact channel?"),
                "action": "核对数量和单位。采购数量未确认前，待核价单保留空白。"}
    if SAMPLES.search(purchase_context):
        return {**result, "mode": "acknowledge", "reason": "客户希望确认样品",
                "draft": ("您好，可以先帮您核对样品。请发目标零件号、所需数量和收货国家，我们确认后回复。"
                          if zh else "We can check sample availability. Please share the part number, sample quantity and destination country."),
                "action": "核对零件号、样品数量、寄送地区及可供情况。"}
    if CATALOG_REQUEST.search(body):
        return {**result, "mode": "acknowledge", "reason": "客户索取产品目录",
                "draft": ("您好，我们有商用车制动件目录。您想看鼓式衬片、盘式刹车片，还是制动蹄总成？"
                          if zh else "We have a commercial-vehicle brake parts catalog. Are you looking for drum brake linings, disc pads, or brake shoe assemblies?"),
                "action": "按客户所需品类发送对应目录，回访目标型号和采购规模。"}
    if fields.get("code") or fields.get("product"):
        draft = ("您好，感谢咨询。请通过冠英官方联系渠道发来旧件编号或车桥型号，销售核实后会跟进。"
                 if zh else "Thank you for your inquiry. Please share the old part number or axle model through Kooying’s official contact channels so our sales team can follow up.")
    return {**result, "mode": "acknowledge", "reason": "由销售确认规格或采购需求", "draft": draft,
            "action": "请客户补全旧件编号、车桥或尺寸。复核匹配和数量后进入核价。"}
