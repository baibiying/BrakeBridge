import re
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

import requests
from flask import current_app

PLATFORMS = {"facebook": "Facebook Page", "linkedin": "LinkedIn 公司页"}
REMOTE_IDS = {"facebook": re.compile(r"\d+(?:_\d+)?"),
              "linkedin": re.compile(r"urn:li:(?:ugcPost|share):\d+")}


class ProviderError(RuntimeError):
    def __init__(self, message, *, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


def graph(path):
    version = current_app.config["FACEBOOK_GRAPH_VERSION"]
    if not re.fullmatch(r"v\d+\.\d+", version):
        raise ProviderError("Meta API 版本配置无效。")
    return f"https://graph.facebook.com/{version}/{path}"


def linkedin_headers():
    return {"Linkedin-Version": current_app.config["SOCIAL_LINKEDIN_VERSION"],
            "X-Restli-Protocol-Version": "2.0.0"}


def call(method, url, token, *, final=False, headers=None, **kwargs):
    try:
        response = requests.request(method, url, headers={"Authorization": "Bearer " + token, **(headers or {})},
                                    timeout=(6, 18), allow_redirects=False, **kwargs)
    except requests.RequestException as error:
        raise ProviderError("平台连接中断。" if not final else "发布回执未确认，请先核对平台原帖。",
                            uncertain=final) from error
    if not 200 <= response.status_code < 300:
        if final and response.status_code >= 500:
            raise ProviderError("平台返回服务错误，发布结果待核对。", uncertain=True)
        if response.status_code in {401, 403}:
            raise ProviderError("平台授权失效或权限不足，请更新帐号授权。")
        if response.status_code == 429:
            raise ProviderError("平台限制请求频率，请稍后重试。")
        raise ProviderError(f"平台拒绝请求（HTTP {response.status_code}）。")
    return response


def json_body(response, final=False):
    try:
        data = response.json()
        if isinstance(data, dict):
            return data
    except ValueError:
        pass
    raise ProviderError("平台响应无法核对。", uncertain=final)


def inspect_account(platform, external_id, token):
    if not external_id.isdecimal() or not 1 <= len(external_id) <= 30:
        raise ValueError("请输入数字形式的主页或组织 ID。")
    if platform == "facebook":
        identity = json_body(call("GET", graph("me"), token, params={"fields": "id,name"}))
        if str(identity.get("id")) != external_id:
            raise ProviderError("Page Token 与填写的 Facebook Page ID 不匹配。")
    else:
        identity = json_body(call("GET", f"https://api.linkedin.com/rest/organizations/{external_id}", token,
                                 headers=linkedin_headers()))
        if str(identity.get("id")) != external_id:
            raise ProviderError("LinkedIn 返回的组织编号与帐号不符。")
    return str(identity.get("name") or identity.get("localizedName") or external_id)[:140]


def content_text(version, platform, media=False):
    text = version[platform].strip()
    if version["url"] and media:
        text += "\n\n" + version["url"]
    return text


def linkedin_image(account, version, token):
    headers = linkedin_headers()
    author = f"urn:li:organization:{account['external_id']}"
    data = json_body(call("POST", "https://api.linkedin.com/rest/images?action=initializeUpload", token,
                          headers=headers, json={"initializeUploadRequest": {"owner": author}}))
    value = data.get("value", {})
    url = str(value.get("uploadUrl") or "")
    uri = urlsplit(url)
    if (uri.scheme != "https" or uri.hostname != "www.linkedin.com" or not uri.path.startswith("/dms-uploads/")
            or uri.username or uri.port not in {None, 443}):
        raise ProviderError("LinkedIn 返回了无法验证的媒体上传地址。")
    image_id = str(value.get("image") or "")
    if not re.fullmatch(r"urn:li:image:[A-Za-z0-9_-]+", image_id):
        raise ProviderError("LinkedIn 返回的媒体 ID 无效。")
    call("PUT", url, token, data=version["image"], headers={"Content-Type": version["image_type"]})
    return image_id


def publish(account, version, token):
    platform = account["platform"]
    image = version["image"]
    text = content_text(version, platform, media=bool(image))
    if platform == "facebook":
        if image:
            response = json_body(call("POST", graph(f"{account['external_id']}/photos"), token, final=True,
                                      data={"caption": text}, files={"source": ("post-image", image, version["image_type"])}), final=True)
        else:
            data = {"message": text}
            if version["url"]:
                data["link"] = version["url"]
            response = json_body(call("POST", graph(f"{account['external_id']}/feed"), token, final=True, data=data), final=True)
        remote = str(response.get("post_id") or response.get("id") or "")
    else:
        post = {"author": f"urn:li:organization:{account['external_id']}", "commentary": text, "visibility": "PUBLIC",
                "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
                "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False}
        if image:
            post["content"] = {"media": {"id": linkedin_image(account, version, token), "title": version["title"]}}
        elif version["url"]:
            post["content"] = {"article": {"source": version["url"], "title": version["title"]}}
        response = call("POST", "https://api.linkedin.com/rest/posts", token, headers=linkedin_headers(), json=post, final=True)
        remote = response.headers.get("x-restli-id", "")
    if not REMOTE_IDS[platform].fullmatch(remote):
        raise ProviderError("平台未返回可核对的帖子 ID，请先检查原帖。", uncertain=True)
    return remote, permalink(platform, remote)


def permalink(platform, remote):
    if platform == "facebook":
        return "https://www.facebook.com/" + remote
    return "https://www.linkedin.com/feed/update/" + remote + "/"


def confirm_post(account, version, remote, token):
    platform = account["platform"]
    if not REMOTE_IDS[platform].fullmatch(remote):
        raise ValueError("请输入平台返回的帖子 ID。")
    if platform == "facebook":
        payload = json_body(call("GET", graph(remote), token, params={"fields": "id,from,message"}))
        author = str((payload.get("from") or {}).get("id") or "")
        text = payload.get("message", "")
    else:
        payload = json_body(call("GET", "https://api.linkedin.com/rest/posts/" + quote(remote, safe=""), token,
                                 headers=linkedin_headers()))
        author = str(payload.get("author") or "").removeprefix("urn:li:organization:")
        text = payload.get("commentary", "")
    if author != account["external_id"] or text.strip() != content_text(version, platform, media=bool(version["image"])):
        raise ProviderError("帖子作者或正文与已批准版本不符。")
    return permalink(platform, remote)


def comments(account, remote, token):
    platform = account["platform"]
    if not REMOTE_IDS[platform].fullmatch(remote):
        raise ProviderError("帖子编号无效。")
    if platform == "facebook":
        return facebook_comments(remote, token)
    return linkedin_comments(remote, token)


def facebook_comments(remote, token):
    output, cursor = [], ""
    for _ in range(5):
        params = {"fields": "id,message,from,created_time", "limit": 100}
        if cursor:
            params["after"] = cursor
        page = json_body(call("GET", graph(remote + "/comments"), token, params=params))
        for item in page.get("data", []):
            if isinstance(item, dict) and item.get("id") and item.get("message"):
                output.append({"comment_id": str(item["id"]), "body": item["message"],
                               "received_at": item.get("created_time", ""), "company": (item.get("from") or {}).get("name", "")})
        paging = page.get("paging") or {}
        cursor = (paging.get("cursors") or {}).get("after") if paging.get("next") else None
        if not cursor:
            break
    return output


def linkedin_comments(remote, token):
    output = []
    path = f"https://api.linkedin.com/rest/socialActions/{quote(remote, safe='')}/comments"
    for offset in range(0, 500, 100):
        page = json_body(call("GET", path, token, headers=linkedin_headers(), params={"start": offset, "count": 100}))
        items = page.get("elements", [])
        for item in items:
            if not isinstance(item, dict):
                continue
            body = (item.get("message") or {}).get("text")
            comment_id = item.get("commentUrn") or item.get("id")
            if comment_id and body:
                created = (item.get("created") or {}).get("time")
                output.append({"comment_id": str(comment_id), "body": body,
                               "received_at": datetime.fromtimestamp(created / 1000, timezone.utc).isoformat()
                               if isinstance(created, int) and 0 < created < 4102444800000 else ""})
        if len(items) < 100:
            break
    return output
