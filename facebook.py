import hashlib
import hmac
import re
from datetime import datetime, timezone

import requests
from flask import current_app


class FacebookUnavailable(Exception):
    pass


class FacebookReplyUncertain(FacebookUnavailable):
    pass


def page_settings():
    cfg = current_app.config
    page = cfg["FACEBOOK_PAGE_ID"]
    post = cfg["FACEBOOK_POST_ID"]
    if not re.fullmatch(r"\d+", page) or not re.fullmatch(re.escape(page) + r"_\d+", post):
        return "", ""
    return page, post


def comments():
    config = current_app.config
    page, post = page_settings()
    version = config["FACEBOOK_GRAPH_VERSION"]
    token = config["FACEBOOK_PAGE_TOKEN"]
    if not all([page, post, token]) or not re.fullmatch(r"v\d+\.\d+", version):
        raise FacebookUnavailable("请先配置团队 Page、产品帖子 ID 和 Page 访问令牌。")
    url = f"https://graph.facebook.com/{version}/{post}/comments"
    parameters = {"fields": "id,message,created_time,permalink_url", "limit": 100}
    results = []
    cursors = set()
    try:
        for _ in range(10):
            response = requests.get(url, params=parameters, headers={"Authorization": f"Bearer {token}"},
                                    timeout=(4, 8), allow_redirects=False)
            if not response.ok:
                error = response.json().get("error", {})
                code = error.get("code")
                suffix = f"（Meta 错误码 {code}）" if type(code) is int else ""
                raise FacebookUnavailable("Facebook 读取失败，请检查 Page 授权或限流状态。" + suffix)
            payload = response.json()
            for entry in payload["data"]:
                if entry.get("id") and entry.get("message"):
                    results.append((len(results) + 2, {
                        "account": page, "post_id": post, "comment_id": entry["id"],
                        "body": entry["message"], "received_at": entry.get("created_time", ""),
                        "source_ref": entry.get("permalink_url") or f"https://www.facebook.com/{post}",
                    }))
            paging = payload.get("paging", {})
            after = paging.get("cursors", {}).get("after")
            if not paging.get("next") or not after or after in cursors:
                return results, False
            cursors.add(after)
            parameters["after"] = after
        return results, True
    except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError) as error:
        raise FacebookUnavailable("暂时无法读取 Facebook 评论，请稍后重试或导入 CSV。") from error


def reply_target(comment_id):
    cfg = current_app.config
    version = cfg["FACEBOOK_GRAPH_VERSION"]
    token = cfg["FACEBOOK_PAGE_TOKEN"]
    if not token or not re.fullmatch(r"v\d+\.\d+", version) or not re.fullmatch(r"\d+(?:_\d+)*", comment_id):
        raise FacebookUnavailable("评论 ID 或 Page 授权需要核对。")
    return f"https://graph.facebook.com/{version}/{comment_id}", {"Authorization": f"Bearer {token}"}


def verify_reply_target(comment_id, original, page_id):
    url, headers = reply_target(comment_id)
    try:
        response = requests.get(url, headers=headers,
                                params={"fields": "id,from,message,can_comment,is_private,parent"},
                                timeout=(4, 8), allow_redirects=False)
        response.raise_for_status()
        data = response.json()
        author = str((data.get("from") or {}).get("id") or "")
    except (requests.RequestException, ValueError, TypeError, AttributeError) as error:
        raise FacebookUnavailable("暂时无法验证评论作者与回复权限。") from error
    if author == page_id:
        raise FacebookUnavailable("这是主页自己的评论。")
    if not author or data.get("can_comment") is not True or data.get("is_private") is True or data.get("parent"):
        raise FacebookUnavailable("该评论需要人工确认回复权限或上下文。")
    if data.get("id") != comment_id or (data.get("message") or "").strip() != original:
        raise FacebookUnavailable("原评论发生变化，请人工核对。")


def publish_reply(comment_id, body):
    url, headers = reply_target(comment_id)
    try:
        response = requests.post(url + "/comments", headers=headers, data={"message": body},
                                 timeout=(4, 10), allow_redirects=False)
    except requests.RequestException as error:
        raise FacebookReplyUncertain("Facebook 发布结果待确认，请先查看原帖。") from error
    if 400 <= response.status_code < 500:
        raise FacebookUnavailable("Facebook 拒绝发布，请检查 Page 的评论管理权限。")
    if response.ok:
        try:
            reply_id = response.json().get("id")
            if isinstance(reply_id, str) and reply_id:
                return reply_id
        except (ValueError, AttributeError):
            pass
    raise FacebookReplyUncertain("Facebook 发布结果待确认，请先查看原帖。")


def verify_signature(body, header):
    secret = current_app.config["FACEBOOK_APP_SECRET"]
    if not secret or not header or not header.isascii():
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


def webhook_rows(payload):
    page, post = page_settings()
    if not page or not isinstance(payload, dict) or payload.get("object") != "page":
        return []
    rows = []
    for entry in payload.get("entry", []):
        if str(entry.get("id")) != page:
            continue
        for change in entry.get("changes", []):
            item = change.get("value", {})
            if (change.get("field") != "feed" or item.get("item") != "comment" or item.get("verb") != "add"
                    or item.get("post_id") != post or not item.get("comment_id") or not item.get("message")):
                continue
            stamp = item.get("created_time")
            date = datetime.fromtimestamp(stamp, timezone.utc).isoformat() if type(stamp) is int else ""
            rows.append((len(rows) + 2, {
                "account": page, "post_id": post, "comment_id": item["comment_id"],
                "body": item["message"], "received_at": date,
                "source_ref": f"https://www.facebook.com/{post}",
            }))
    return rows
