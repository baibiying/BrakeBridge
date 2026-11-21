import csv
import hashlib
import json
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree

import requests
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from flask import current_app

import inquiries
import lead_inquiries as leads
import research_agent
import social_assets
from model_client import ModelUnavailable, ask_json, available
import social_providers as providers
from storage import get_db, write_transaction

COMPANY_SOURCE = {"title": "冠英官方产品目录", "url": "https://www.kooying.com/product/",
                  "summary": "冠英提供商用车制动衬片、盘式刹车片和制动蹄等产品。展会和技术信息以已核资料为准。"}
COMPANY_PROFILE = {
    "title": "2026 年浙江冠英企业介绍",
    "url": "",
    "summary": "浙江冠英汽车零部件有限公司（ZHEJIANG KOOYING AUTOPARTS CO., LTD.）位于浙江湖州，"
               "始创于 2006 年，开展商用车盘式及鼓式刹车片研发、生产和服务。依据企业介绍第 2 页。",
}
NEWS_DAYS = 21
PLATFORMS = providers.PLATFORMS
JOB_STATES = {"scheduled": "待发布", "sending": "发布中", "sent": "已发布", "failed": "失败",
              "uncertain": "待核对", "cancelled": "已取消", "blocked": "需重新连接"}
MAX_IMAGE = 4 * 1024 * 1024
DEMO_CHANNEL = "social_demo"
DEMO_ACCOUNT = "brakebridge"
DEMO_POST = "cn-comments"
TOPICS = {
    "catalog": {"zh": ("冠英商用车制动件", "冠英提供商用车制动衬片、盘式刹车片及制动蹄。欢迎重卡配件采购商提供车型、零件号和计划采购量，与我们沟通选型。"),
                "en": ("Commercial vehicle brake parts", "Kooying supplies brake linings, disc brake pads and brake shoes for commercial vehicles. Share your part number, vehicle model and target quantity so our team can check the catalog.")},
    "distribution": {"zh": ("重卡配件经销合作", "欢迎商用车零部件经销商和进口商与冠英交流制动件需求。提供目标市场、车型与零件号，便于我们核对产品目录。"),
                     "en": ("Brake parts for distributors", "Distributing commercial vehicle parts? Tell Kooying your target market, vehicle models and brake part numbers so we can help identify catalog options.")},
    "meeting": {"zh": ("预约商用车制动件交流", "正在寻找商用车制动衬片、刹车片或制动蹄？欢迎联系冠英预约采购交流，提前发来车型、零件号和需求。"),
                "en": ("Meet Kooying to discuss brake parts", "Sourcing brake linings, pads or shoes for commercial vehicles? Share your vehicle models and part numbers with Kooying to arrange a product discussion.")},
}


def utc_stamp(value=None):
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")


def text(value, limit, label):
    value = str(value or "").strip()
    if len(value) > limit:
        raise ValueError(f"{label}不能超过 {limit} 字。")
    return value


def public_url(value):
    if not value:
        return ""
    try:
        url = urlsplit(value)
        if url.scheme == "https" and url.hostname and not url.username and url.port in {None, 443}:
            return value
    except ValueError:
        pass
    raise ValueError("请输入公开的 HTTPS 链接。")


def cipher():
    database = Path(current_app.config["DATABASE"]).resolve()
    managed = database.with_name(database.name + ".social-token.key")
    legacy = database.with_name("social-token.key")
    configured = (current_app.config.get("SOCIAL_TOKEN_KEY") or "").encode()
    if configured:
        try:
            Fernet(configured)
        except ValueError:
            configured = b""
    try:
        if not managed.is_file():
            with write_transaction(get_db()):
                if not managed.is_file():
                    material = configured or (legacy.read_bytes().strip() if legacy.is_file() else Fernet.generate_key())
                    Fernet(material)
                    with tempfile.NamedTemporaryFile(dir=managed.parent, prefix=managed.name + ".", delete=False) as handle:
                        temporary = Path(handle.name)
                        try:
                            handle.write(material)
                            handle.flush()
                            os.fsync(handle.fileno())
                        except OSError:
                            handle.close()
                            temporary.unlink(missing_ok=True)
                            raise
                    try:
                        os.replace(temporary, managed)
                    finally:
                        temporary.unlink(missing_ok=True)
        primary = managed.read_bytes().strip()
        keys = [Fernet(primary)]
        if configured and configured != primary:
            keys.append(Fernet(configured))
        return MultiFernet(keys)
    except (OSError, ValueError) as error:
        raise ValueError("无法读取本机凭据存储，请检查工作台数据目录的读写权限。") from error


def credential(account):
    if not account["active"]:
        raise ValueError("帐号已停用，请先重新连接。")
    try:
        return cipher().decrypt(account["credential"].encode()).decode()
    except (InvalidToken, UnicodeError) as error:
        raise ValueError("帐号凭据不可用，请重新连接。") from error


def accounts():
    return [dict(row) for row in get_db().execute("""SELECT id,platform,external_id,name,mode,active,
                                                      publish_check,comment_check,checked_at FROM social_accounts
                                                      ORDER BY mode DESC,platform,name""")]


def enroll(platform, external_id, token, mode):
    if platform not in PLATFORMS or mode not in {"demo", "live"}:
        raise ValueError("请选择支持的社媒平台。")
    check = "unverified"
    if mode == "demo":
        external_id = "demo-" + platform
        name = PLATFORMS[platform] + " 演示帐号"
        encrypted = ""
        check = "demo"
    else:
        if not token or len(token) > 7000:
            raise ValueError("请填入平台授权发放的访问令牌。")
        encrypted = cipher().encrypt(token.encode()).decode()
        name = providers.inspect_account(platform, external_id, token)
    db = get_db()
    with db:
        db.execute("""INSERT INTO social_accounts(platform,external_id,name,mode,credential,active,
                       publish_check,comment_check,checked_at) VALUES (?,?,?,?,?,1,?,?,?)
                       ON CONFLICT(platform,external_id,mode) DO UPDATE SET
                       name=excluded.name,credential=excluded.credential,active=1,publish_check=excluded.publish_check,
                       comment_check=excluded.comment_check,checked_at=excluded.checked_at""",
                   (platform, external_id, name, mode, encrypted, check, check, utc_stamp()))
    return next(row for row in accounts() if (row["platform"], row["external_id"], row["mode"]) == (platform, external_id, mode))


def deactivate(account_id):
    db = get_db()
    with db:
        changed = db.execute("UPDATE social_accounts SET active=0,credential='' WHERE id=?", (account_id,)).rowcount
        db.execute("""UPDATE social_jobs SET state='blocked',note='帐号已停用',updated_at=?
                      WHERE account_id=? AND state='scheduled'""", (utc_stamp(), account_id))
    return changed


def store_news(title, url, published_on, summary):
    title = text(title, 160, "标题")
    url = public_url(text(url, 1600, "来源链接"))
    summary = text(summary, 1600, "要点")
    if not title or not url or not summary:
        raise ValueError("请填写新闻标题、链接和可核实的要点。")
    published = date.fromisoformat(published_on)
    if published > datetime.now(leads.ZONE).date():
        raise ValueError("新闻发布日期不能晚于今天。")
    db = get_db()
    with write_transaction(db):
        db.execute("""INSERT INTO social_news(title,url,published_on,summary) VALUES (?,?,?,?)
                      ON CONFLICT(url) DO UPDATE SET title=excluded.title,published_on=excluded.published_on,summary=excluded.summary""",
                   (title, url, published.isoformat(), summary))


def news():
    today = datetime.now(leads.ZONE).date()
    return [dict(row) for row in get_db().execute("""SELECT * FROM social_news WHERE published_on BETWEEN ? AND ?
                                                      ORDER BY published_on DESC,id DESC LIMIT 40""",
                                                  ((today - timedelta(days=NEWS_DAYS - 1)).isoformat(), today.isoformat()))]


def node_text(element, path):
    result = element.find(path)
    return "".join(result.itertext()).strip() if result is not None else ""


def refresh_news():
    feeds = current_app.config["SOCIAL_NEWS_FEEDS"].split(",")
    added, failed = 0, 0
    for source in [item.strip() for item in feeds if item.strip()][:5]:
        try:
            with requests.get(public_url(source), timeout=(5, 8), allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    raise ValueError("请求失败")
                parts = bytearray()
                for chunk in response.iter_content(65536):
                    parts.extend(chunk)
                    if len(parts) > 1024 * 1024:
                        raise ValueError("来源文件过大")
            if any(item in parts.upper() for item in (b"<!DOCTYPE", b"<!ENTITY")):
                raise ValueError("来源 XML 含不支持的声明")
            root = ElementTree.fromstring(parts)
            for item in (root.findall(".//item") + root.findall("{*}entry"))[:30]:
                try:
                    title = node_text(item, "{*}title")
                    link = item.find("{*}link")
                    url = (link.get("href") or (link.text or "").strip()) if link is not None else ""
                    parsed = leads.date_value(node_text(item, "{*}pubDate") or node_text(item, "{*}published"))
                    description = node_text(item, "{*}description") or node_text(item, "{*}summary")
                    if not parsed:
                        continue
                    published = datetime.fromisoformat(parsed).astimezone(leads.ZONE).date()
                    if published < datetime.now(leads.ZONE).date() - timedelta(days=NEWS_DAYS - 1):
                        continue
                    html = leads.PlainHTML()
                    html.feed(description[:12000])
                    store_news(title[:160], url, published.isoformat(), "".join(html.parts).strip()[:1600])
                    added += 1
                except ValueError:
                    continue
        except (requests.RequestException, ElementTree.ParseError, ValueError):
            failed += 1
    return {"added": added, "failed": failed}


def sources(selected, saved=()):
    selected = {int(value) for value in selected}
    snapshots = {item["id"]: item for item in [*news(), *saved] if item.get("id") in selected}
    return [COMPANY_SOURCE, COMPANY_PROFILE, *snapshots.values()]


def polish(title, body, selected, event):
    title = text(title, 160, "标题")
    body = text(body, 2800, "企业原稿")
    refs = sources(selected)
    if not title or not body:
        raise ValueError("请填写标题和企业原稿。")
    if available():
        try:
            output = ask_json(
                "为冠英生成 Facebook Page 和 LinkedIn 企业帖草稿。返回 facebook 和 linkedin 两个字符串，每个最多2800字。"
                "沿用企业原稿语言，Facebook简洁、LinkedIn偏采购和行业语境。仅使用原稿和提供的证据。"
                "新闻内容仅摘要并注明出处，不复制全文，不新增价格、认证、性能、交期、规格、展位或参展承诺。"
                "不输出额外链接；用新闻来源名称注明出处，来源链接会保存在稿件里供审核。",
                {"title": title, "body": body, "sources": refs, "verified_event": event},
            )
            if all(isinstance(output.get(key), str) and 0 < len(output[key].strip()) <= 2800 for key in PLATFORMS):
                return {**{key: output[key].strip() for key in PLATFORMS}, "refs": refs, "engine": "ai"}
        except ModelUnavailable:
            pass
    return {"facebook": body, "linkedin": body, "refs": refs, "engine": "manual"}


def record_article_news(items, cited, today):
    message = "本次热点来源无法完整核对。可关闭联网后使用工作台已收录的热点重新生成。"
    if not isinstance(items, list) or len(items) > 3:
        raise ValueError(message)
    earliest = today - timedelta(days=NEWS_DAYS - 1)
    db = get_db()
    verified = {}
    try:
        with write_transaction(db):
            for item in items:
                url = public_url(text(item["url"], 1600, "来源链接"))
                actual_url, _ = cited[research_agent.source_key(url)]
                published = date.fromisoformat(item["published_on"])
                if not earliest <= published <= today:
                    raise ValueError(message)
                store_news(item["title"], actual_url, published.isoformat(), item["summary"])
                verified[actual_url] = dict(db.execute("SELECT * FROM social_news WHERE url=?", (actual_url,)).fetchone())
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise ValueError(message) from error
    return list(verified.values())


def generate_article(topic, language, title, body, selected, event, *, use_images=False, live_news=False):
    if topic not in TOPICS or language not in TOPICS[topic]:
        raise ValueError("请选择文章主题和语言。")
    default_title, default_body = TOPICS[topic][language]
    title = text(title, 160, "标题") or default_title
    body = text(body, 2800, "企业原稿") or default_body
    recent = news()
    refs = sources(selected or [item["id"] for item in recent[:3]])
    assets = social_assets.catalog() if use_images else []
    if research_agent.settings()["configured"]:
        today = datetime.now(leads.ZONE).date()
        prompt = (
            "为浙江冠英写一条国际商用车制动件社媒推广草稿。按语言输出 title 和 body 两个字符串，正文最多 2800 字。"
            "企业事实应来自资料；正文要让商用车制动件进口商或分销商知道下一步可提供哪些信息。"
            "结合真正相关的行业热点并标明来源名称，无法核实就省略。严禁承诺未经核实的展位、参展、认证、适配、价格、交期。"
            "配图仅从素材中选一张与正文直接匹配的产品图，返回整数 asset_id；没有合适的图就返回 null。"
        )
        search = live_news and not selected
        if search:
            prompt += (
                "使用联网工具搜索最近三周内与商用车、汽配后市场或上海汽配展相关的主办方或机构原始发布。"
                "只用公开证据，查证发布日期而非活动日期。news 数组只列正文实际采用的新增来源，最多 3 条，"
                "每条包含 title、url（搜索工具返回的原始 URL）、published_on（YYYY-MM-DD）、summary（中文要点）。"
                "找不到可验证来源，正文只使用给定资料，news 返回空数组。"
            )
        try:
            output, cited = research_agent.ask(prompt, {
                "title": title, "original": body, "language": language, "sources": refs,
                "verified_event": event, "素材": assets, "today": today.isoformat(),
            }, web=search)
        except research_agent.ResearchUnavailable as error:
            raise ValueError(str(error)) from error
        if not all(isinstance(output.get(key), str) and 0 < len(output[key].strip()) <= limit
                   for key, limit in (("title", 160), ("body", 2800))):
            raise ValueError("销售 Agent 未返回有效的文章草稿，请重试。")
        items = output.get("news", [])
        if search and items:
            verified = record_article_news(items, cited, today)
            refs = list({item["url"]: item for item in [*refs, *verified]}.values())
        asset_id = output.get("asset_id")
        return {"title": output["title"].strip(), "body": output["body"].strip(), "refs": refs, "engine": "agent",
                "asset_id": asset_id if type(asset_id) is int and any(item["id"] == asset_id for item in assets) else None}
    if available() and not use_images:
        try:
            output = ask_json(
                "为冠英写一条可跨平台发布的商用车制动件推广短文。只返回 title 和 body 两个字符串。"
                "按指定语言输出。保留产品事实和已核实的时间地点，用易懂的采购术语。正文尽量不超过 240 字符。"
                "新闻只摘要且注明出处。不要增加未核实的认证、价格、型号、交期或展会承诺。",
                {"title": title, "original": body, "language": language, "sources": refs, "verified_event": event},
            )
            if all(isinstance(output.get(key), str) and 0 < len(output[key].strip()) <= limit
                   for key, limit in (("title", 160), ("body", 2800))):
                return {"title": output["title"].strip(), "body": output["body"].strip(), "refs": refs, "engine": "ai"}
        except ModelUnavailable:
            pass
    return {"title": title, "body": body, "refs": refs, "engine": "preset"}


def image_payload(upload):
    if upload is None or not upload.filename:
        return "", None
    data = upload.read(MAX_IMAGE + 1)
    if len(data) > MAX_IMAGE:
        raise ValueError("图片需小于 4 MB。")
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", data
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", data
    raise ValueError("请上传一张 JPG 或 PNG。")


def save_draft(draft_id, title, facebook, linkedin, url, refs, photo, keep_image, *, asset_id=None):
    title = text(title, 160, "标题")
    facebook = text(facebook, 2800, "Facebook 文案")
    linkedin = text(linkedin, 2800, "LinkedIn 文案")
    url = public_url(text(url, 1600, "发布链接"))
    if not title or not (facebook or linkedin):
        raise ValueError("请填写标题和至少一个平台的文案。")
    db = get_db()
    now = utc_stamp()
    if asset_id is not None:
        photo = social_assets.attachment(asset_id)
    with write_transaction(db):
        if draft_id is None:
            draft_id = db.execute("INSERT INTO social_drafts(title,created_at,updated_at) VALUES (?,?,?)",
                                  (title, now, now)).lastrowid
            revision = 1
        else:
            draft = db.execute("SELECT * FROM social_drafts WHERE id=?", (draft_id,)).fetchone()
            if draft is None:
                raise ValueError("稿件不存在。")
            revision = draft["current_version"] + 1
            if keep_image and photo[1] is None:
                old = db.execute("SELECT image_type,image,asset_id FROM social_versions WHERE draft_id=? AND revision=?",
                                 (draft_id, draft["current_version"])).fetchone()
                if old:
                    photo = (old["image_type"], old["image"])
                    asset_id = old["asset_id"]
            db.execute("""UPDATE social_jobs SET state='cancelled',note='稿件已改动，请审核新版本',updated_at=?
                          WHERE version_id IN (SELECT id FROM social_versions WHERE draft_id=?)
                            AND state IN ('scheduled','failed','blocked')""", (now, draft_id))
            db.execute("""UPDATE figeon_batches SET state='cancelled',note='稿件已改动，请审核新版本',updated_at=?
                          WHERE version_id IN (SELECT id FROM social_versions WHERE draft_id=?) AND state='scheduled'""",
                       (now, draft_id))
        version_id = db.execute("""INSERT INTO social_versions(draft_id,revision,title,facebook,linkedin,url,
                                  image_type,image,refs,created_at,asset_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                                (draft_id, revision, title, facebook, linkedin, url, *photo,
                                 json.dumps(refs, ensure_ascii=False), now, asset_id)).lastrowid
        db.execute("UPDATE social_drafts SET title=?,current_version=?,updated_at=? WHERE id=?", (title, revision, now, draft_id))
    return {"id": draft_id, "version_id": version_id, "revision": revision}


def drafts():
    return [dict(row) for row in get_db().execute("""SELECT d.id, d.title, d.current_version, d.updated_at,
            v.id AS version_id,v.approved_at FROM social_drafts d JOIN social_versions v
            ON v.draft_id=d.id AND v.revision=d.current_version ORDER BY d.updated_at DESC,d.id DESC""")]


def draft_detail(draft_id):
    row = get_db().execute("""SELECT v.id AS version_id,v.draft_id AS id,v.revision,v.title,v.facebook,v.linkedin,v.url,
                              v.refs,v.approved_at,v.asset_id,v.image IS NOT NULL AS has_image FROM social_drafts d
                              JOIN social_versions v ON v.draft_id=d.id AND v.revision=d.current_version WHERE d.id=?""",
                           (draft_id,)).fetchone()
    return ({**dict(row), "refs": json.loads(row["refs"])} if row else None)


def approved_version(version_id):
    row = get_db().execute("""SELECT v.* FROM social_versions v JOIN social_drafts d
                              ON d.id=v.draft_id AND d.current_version=v.revision WHERE v.id=?""",
                           (version_id,)).fetchone()
    if row is None:
        raise ValueError("稿件已更新，请打开最新版本。")
    return row


def approve(version_id):
    db = get_db()
    with write_transaction(db):
        version = approved_version(version_id)
        if not version["approved_at"]:
            db.execute("UPDATE social_versions SET approved_at=? WHERE id=?", (utc_stamp(), version_id))


def schedule_time(when):
    try:
        scheduled = datetime.fromisoformat(when)
    except ValueError as error:
        raise ValueError("请填写有效的发布时间。") from error
    if scheduled.tzinfo is None:
        scheduled = scheduled.replace(tzinfo=leads.ZONE)
    if scheduled < datetime.now(timezone.utc) - timedelta(minutes=1):
        raise ValueError("请选择当前或未来的发布时间。")
    return utc_stamp(scheduled)


def schedule(version_id, ids, when):
    if not ids or len(ids) > 30:
        raise ValueError("请选择至少一个发布帐号。")
    scheduled = schedule_time(when)
    db = get_db()
    count = 0
    with db:
        db.execute("BEGIN IMMEDIATE")
        version = approved_version(version_id)
        if not version["approved_at"]:
            raise ValueError("请先批准当前稿件版本。")
        for account_id in set(ids):
            account = db.execute("SELECT * FROM social_accounts WHERE id=? AND active=1", (account_id,)).fetchone()
            if not account:
                raise ValueError("所选帐号未连接。")
            if not version[account["platform"]]:
                raise ValueError("请补齐所选平台的文案。")
            count += db.execute("""INSERT INTO social_jobs(version_id,account_id,run_at,state,updated_at)
                                  VALUES (?,?,?,'scheduled',?) ON CONFLICT(version_id,account_id) DO NOTHING""",
                                (version_id, account_id, scheduled, utc_stamp())).rowcount
    return count


def jobs():
    return [dict(row) for row in get_db().execute("""SELECT j.*,a.platform,a.name,a.mode,v.title,v.revision,v.draft_id
                        FROM social_jobs j JOIN social_accounts a ON a.id=j.account_id JOIN social_versions v ON v.id=j.version_id
                        ORDER BY j.id DESC LIMIT 250""")]


def job_record(job_id):
    db = get_db()
    job = db.execute("SELECT * FROM social_jobs WHERE id=?", (job_id,)).fetchone()
    if not job:
        raise ValueError("发布任务不存在。")
    account = db.execute("SELECT * FROM social_accounts WHERE id=?", (job["account_id"],)).fetchone()
    version = db.execute("SELECT * FROM social_versions WHERE id=?", (job["version_id"],)).fetchone()
    return job, account, version


def update_job(job_id, state, note="", remote_id="", remote_url=""):
    db = get_db()
    with db:
        db.execute("""UPDATE social_jobs SET state=?,note=?,remote_id=?,remote_url=?,updated_at=?
                      WHERE id=? AND state IN ('sending','uncertain')""",
                   (state, note, remote_id, remote_url, utc_stamp(), job_id))


def run_due(limit=10, demo_only=False):
    db = get_db()
    finished = 0
    with db:
        db.execute("""UPDATE social_jobs SET state='uncertain',note='任务中断，先核对原帖',updated_at=?
                      WHERE state='sending' AND updated_at<?""",
                   (utc_stamp(), utc_stamp(datetime.now(timezone.utc) - timedelta(minutes=3))))
    for _ in range(limit):
        with db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT j.id FROM social_jobs j JOIN social_accounts a ON a.id=j.account_id
                                WHERE j.state='scheduled' AND j.run_at<=? AND (?=0 OR a.mode='demo')
                                ORDER BY j.run_at,j.id LIMIT 1""", (utc_stamp(), int(demo_only))).fetchone()
            if not row:
                break
            db.execute("UPDATE social_jobs SET state='sending',updated_at=? WHERE id=?", (utc_stamp(), row["id"]))
        job, account, version = job_record(row["id"])
        current = db.execute("SELECT current_version FROM social_drafts WHERE id=?", (version["draft_id"],)).fetchone()
        if not account["active"] or not version["approved_at"] or version["revision"] != current["current_version"]:
            update_job(job["id"], "blocked", "请核对帐号授权和已批准稿件版本。")
            continue
        try:
            if account["mode"] == "demo":
                remote, link = "demo-post-" + str(job["id"]), ""
            else:
                remote, link = providers.publish(account, version, credential(account))
            update_job(job["id"], "sent", "演示发布" if account["mode"] == "demo" else "", remote, link)
            with db:
                if account["mode"] == "live":
                    db.execute("UPDATE social_accounts SET publish_check='confirmed',checked_at=? WHERE id=?",
                               (utc_stamp(), account["id"]))
            finished += 1
        except (providers.ProviderError, ValueError) as error:
            update_job(job["id"], "uncertain" if getattr(error, "uncertain", False) else "failed", str(error))
    return finished


def manage_job(job_id, action, remote=""):
    job, account, version = job_record(job_id)
    db = get_db()
    if action == "confirm" and job["state"] == "uncertain" and account["mode"] == "live":
        link = providers.confirm_post(account, version, remote, credential(account))
        update_job(job_id, "sent", "已按远端帐号和正文核对", remote, link)
        with db:
            db.execute("UPDATE social_accounts SET publish_check='confirmed',checked_at=? WHERE id=?",
                       (utc_stamp(), account["id"]))
        return
    if action == "cancel" and job["state"] in {"scheduled", "blocked", "failed"}:
        target = "cancelled"
    elif action == "retry" and job["state"] in {"failed", "blocked", "cancelled"} and account["active"]:
        approved_version(job["version_id"])
        target = "scheduled"
    else:
        raise ValueError("当前状态不支持此操作。")
    with db:
        changed = db.execute("UPDATE social_jobs SET state=?,note='',updated_at=? WHERE id=? AND state=?",
                             (target, utc_stamp(), job_id, job["state"])).rowcount
    if not changed:
        raise ValueError("任务状态已变化，请刷新后重试。")


def sync_comments(job_id):
    job, account, _ = job_record(job_id)
    if job["state"] != "sent" or not job["remote_id"]:
        raise ValueError("已发布并有回执后才能同步评论。")
    db = get_db()
    with db:
        db.execute("UPDATE social_jobs SET comments_attempt_at=? WHERE id=?", (utc_stamp(), job_id))
    try:
        if account["mode"] == "demo":
            rows = [{"comment_id": f"demo-{job_id}-{index}", "body": body, "company": "演示互动"}
                    for index, body in enumerate(("视频很清楚，点赞！", "请问有没有卡车盘式刹车片？能发目录吗？",
                                                 "我们是卡车配件进口商，想订 200 套 T850 鼓式刹车片，请报价。"))]
        else:
            rows = providers.comments(account, job["remote_id"], credential(account))
    except (providers.ProviderError, ValueError) as error:
        with db:
            db.execute("UPDATE social_jobs SET comments_error=? WHERE id=?", (str(error), job_id))
        raise
    kind = "synthetic" if account["mode"] == "demo" else "authorized"
    batch = [(line, {**row, "account": account["external_id"], "post_id": job["remote_id"], "source_ref": job["remote_url"]})
             for line, row in enumerate(rows, 1)]
    result = inquiries.import_rows(batch, kind, transport="social", channel=account["platform"])
    with db:
        db.execute("UPDATE social_jobs SET comments_at=?,comments_error='' WHERE id=?", (utc_stamp(), job_id))
        db.execute("UPDATE social_accounts SET comment_check=?,checked_at=? WHERE id=?",
                   ("demo" if account["mode"] == "demo" else "confirmed", utc_stamp(), account["id"]))
    return result


def sync_recent(limit=3):
    now = datetime.now(timezone.utc)
    ids = get_db().execute("""SELECT j.id FROM social_jobs j JOIN social_accounts a ON a.id=j.account_id
                            WHERE j.state='sent' AND a.active=1 AND a.mode='live'
                              AND j.updated_at>=? AND j.comments_attempt_at<?
                            ORDER BY j.comments_attempt_at,j.id LIMIT ?""",
                          (utc_stamp(now - timedelta(days=7)), utc_stamp(now - timedelta(minutes=5)), limit))
    for row in ids.fetchall():
        try:
            sync_comments(row["id"])
        except (providers.ProviderError, ValueError):
            pass


def comment_rows(job_id):
    job, account, _ = job_record(job_id)
    if job["state"] != "sent":
        return []
    return scored_comments(account["platform"], account["external_id"], job["remote_id"])


def scored_comments(channel, account, post_id):
    rows = get_db().execute("SELECT * FROM interactions WHERE channel=? AND account=? AND post_id=?",
                            (channel, account, post_id))
    return scored_rows(rows)


def demo_comments():
    fixture = Path(__file__).parent / "data" / "social-demo-comments.csv"
    with fixture.open(encoding="utf-8", newline="") as source:
        rows = [(line, {"comment_id": row["id"], "body": row["body"], "company": row["author"],
                        "account": DEMO_ACCOUNT, "post_id": DEMO_POST, "received_at": "2026-11-21T12:00:00+00:00"})
                for line, row in enumerate(csv.DictReader(source), 2)]
    inquiries.import_rows(rows, "synthetic", transport="social", channel=DEMO_CHANNEL)
    return scored_comments(DEMO_CHANNEL, DEMO_ACCOUNT, DEMO_POST)


def facebook_history(status="", kind="", selected=None):
    rows = get_db().execute("""SELECT * FROM interactions WHERE channel='facebook'
                               AND (?='' OR status=?) AND (?='' OR data_kind=?) AND (? IS NULL OR id=?)
                               ORDER BY received_at DESC,id DESC LIMIT 300""",
                            (status, status, kind, kind, selected, selected))
    return scored_rows(rows)


def scored_rows(rows):
    db = get_db()
    items = []
    for row in rows:
        analysis = inquiries.read_analysis(row)
        item = {"id": row["id"], "body": row["body"], "author": row["company"], "received_at": row["received_at"],
                "analysis": analysis, "data_kind": row["data_kind"], "source_url": row["source_ref"]}
        if analysis["tier"] == "high":
            lead = db.execute("""SELECT id FROM lead_inquiries WHERE source='social' AND external_id=?
                                 AND data_kind=? AND deleted_at=''""", (comment_key(row), row["data_kind"])).fetchone()
            if lead is not None:
                item["lead_id"] = lead["id"]
        items.append(item)
    return sorted(items, key=lambda row: (-row["analysis"]["score"]["total"], -row["id"]))


def comment_key(comment):
    return hashlib.sha256(json.dumps([comment["channel"], comment["account"], comment["external_id"]]).encode()).hexdigest()


def promote(job_id, comment_id):
    job, account, version = job_record(job_id)
    if job["state"] != "sent":
        raise ValueError("请先确认帖子已发布。")
    return promote_comment(comment_id, account["platform"], account["external_id"], job["remote_id"],
                           version["title"], PLATFORMS[account["platform"]])


def promote_comment(comment_id, channel, account, post_id, title, source):
    comment = get_db().execute("""SELECT * FROM interactions WHERE id=? AND channel=? AND account=? AND post_id=?""",
                               (comment_id, channel, account, post_id)).fetchone()
    parsed = inquiries.read_analysis(comment) if comment else {}
    if parsed.get("tier") != "high":
        raise ValueError("请选择该帖子的一条高意向评论。")
    fields = leads.vendor_fields({"name": comment["company"], "title": title, "content": comment["body"],
                                  "source": source + " 评论", "page": comment["source_ref"],
                                  "addtime": comment["received_at"]})
    external = comment_key(comment)
    if not fields["number"]:
        fields["number"] = "SOC-" + external[:16]
    summary = leads.result("hot", "社媒评论达到高意向（" + str(parsed["score"]["total"]) + " 分）",
                           "核对买家身份，回复或安排销售跟进", parsed.get("quote", ""))
    db = get_db()
    with db:
        result = db.execute("""INSERT INTO lead_inquiries(source,external_id,data_kind,fields,received_at,analysis,next_action)
                    VALUES ('social',?,?,?,?,?,?) ON CONFLICT(source,external_id,data_kind) DO NOTHING""",
                            (external, comment["data_kind"], json.dumps(fields, ensure_ascii=False), comment["received_at"],
                             json.dumps(summary, ensure_ascii=False), summary["next_action"]))
        inquiry = db.execute("SELECT id,deleted_at FROM lead_inquiries WHERE source='social' AND external_id=? AND data_kind=?",
                             (external, comment["data_kind"])).fetchone()
        if inquiry["deleted_at"]:
            raise ValueError("这条评论对应的询盘已删除。")
    return {"id": inquiry["id"], "duplicate": not result.rowcount}
