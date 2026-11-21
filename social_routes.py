import ipaddress
from functools import wraps
from time import sleep

import click
from flask import Response, abort, request

import figeon
import research_agent
import social
import social_assets
import social_verification as verification
from model_client import available
from social_providers import ProviderError
from storage import get_db
from workspace import current_user, error


def handle(operation):
    @wraps(operation)
    def wrapped(*args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except ProviderError as problem:
            return error(str(problem), 502)
        except ValueError as problem:
            return error(str(problem))
    return wrapped


def protected_transport():
    if request.is_secure:
        return True
    try:
        return (url_hostname(request.host) in {"localhost", "127.0.0.1", "::1"}
                and ipaddress.ip_address(request.remote_addr or "").is_loopback)
    except ValueError:
        return False


def url_hostname(host):
    return host.split("]")[0][1:] if host.startswith("[") else host.split(":")[0]


def selected_ids(key):
    values = request.form.getlist(key)
    if len(values) > 40 or any(not value.isascii() or not value.isdecimal() or len(value) > 16 for value in values):
        raise ValueError("请刷新后重新选择。")
    return [int(value) for value in values]


def register(app, event_details):
    @app.get("/api/social")
    def social_overview():
        agent = research_agent.settings()
        agent_ready = agent["configured"]
        return {"drafts": social.drafts(), "news": social.news(), "model_ready": agent_ready or available(),
                "agent_ready": agent_ready, "web_ready": agent.get("web_ready", agent_ready),
                "assets": social_assets.catalog(),
                "news_feeds": bool(app.config["SOCIAL_NEWS_FEEDS"])}

    @app.get("/api/social/assets/<int:asset_id>/image")
    def asset_image(asset_id):
        asset = social_assets.get(asset_id)
        if asset is None:
            abort(404)
        return Response(asset["image"], mimetype="image/jpeg",
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"})

    @app.post("/api/social/news")
    @handle
    def social_add_news():
        data = request.form
        social.store_news(*(data.get(key, "") for key in ("title", "url", "published_on", "summary")))
        return {"ok": True}

    @app.post("/api/social/news/refresh")
    def social_refresh_news():
        return social.refresh_news()

    @app.post("/api/social/polish")
    @handle
    def social_polish():
        return social.polish(request.form.get("title", ""), request.form.get("body", ""),
                             selected_ids("news_ids"), event_details(app))

    @app.post("/api/social/generate-article")
    @handle
    def social_generate_article():
        data = request.form
        return social.generate_article(data.get("topic", "catalog"), data.get("language", "en"),
                                       data.get("title", ""), data.get("body", ""), selected_ids("news_ids"),
                                       event_details(app), use_images=data.get("use_images") == "1",
                                       live_news=data.get("live_news") == "1", short_x="twitter" in data.getlist("platforms"))

    @app.get("/api/social/drafts/<int:draft_id>")
    def social_detail(draft_id):
        detail = social.draft_detail(draft_id)
        if detail is None:
            abort(404)
        return detail

    @app.post("/api/social/drafts", defaults={"draft_id": None})
    @app.post("/api/social/drafts/<int:draft_id>")
    @handle
    def social_save(draft_id):
        data = request.form
        current = social.draft_detail(draft_id) if draft_id is not None else None
        refs = social.sources(selected_ids("news_ids"), current["refs"] if current else ())
        return social.save_draft(draft_id, *(data.get(key, "") for key in ("title", "facebook", "linkedin", "url")), refs,
                                 social.image_payload(request.files.get("image")), data.get("keep_image") == "1",
                                 asset_id=data.get("asset_id", type=int))

    @app.get("/api/social/versions/<int:version_id>/image")
    def social_image(version_id):
        image = get_db().execute("SELECT image,image_type FROM social_versions WHERE id=?", (version_id,)).fetchone()
        if image is None or image["image"] is None:
            abort(404)
        return Response(image["image"], mimetype=image["image_type"], headers={"X-Content-Type-Options": "nosniff"})

    @app.post("/api/social/versions/<int:version_id>/approve")
    @handle
    def social_approve(version_id):
        social.approve(version_id)
        return {"ok": True}

    @app.post("/api/social/comments/demo")
    def social_comment_demo():
        return {"items": social.demo_comments()}

    @app.post("/api/social/comments/demo/promote/<int:comment_id>")
    @handle
    def social_demo_promote(comment_id):
        return social.promote_comment(comment_id, social.DEMO_CHANNEL, social.DEMO_ACCOUNT, social.DEMO_POST,
                                      "中文社媒演示评论", "社媒演示")

    @app.get("/api/social/comments/history")
    def social_history():
        return {"items": social.facebook_history(request.args.get("status", ""), request.args.get("kind", ""),
                                                 request.args.get("message", type=int))}

    @app.post("/api/social/comments/history/promote/<int:comment_id>")
    @handle
    def social_history_promote(comment_id):
        row = get_db().execute("SELECT account,post_id FROM interactions WHERE id=? AND channel='facebook'",
                               (comment_id,)).fetchone()
        if row is None:
            abort(404)
        return social.promote_comment(comment_id, "facebook", row["account"], row["post_id"],
                                      "Facebook 历史评论", "Facebook 历史")

    @app.get("/api/social/figeon")
    def figeon_overview():
        return figeon.snapshot()

    @app.post("/api/social/figeon/check")
    def figeon_health():
        return figeon.check()

    @app.post("/api/social/figeon/keepalive")
    @handle
    def figeon_keepalive():
        enabled = request.form.get("enabled")
        if enabled not in {"0", "1"}:
            raise ValueError("请选择开启或关闭保活。")
        status = figeon.set_keepalive(enabled == "1")
        wake = app.extensions.get("figeon_keepalive_wake")
        if wake is not None:
            wake.set()
        return status

    @app.post("/api/social/figeon/binding")
    @handle
    def figeon_binding():
        if not protected_transport():
            return error("请通过 HTTPS 或本机工作台打开飞鸽绑定页。", 403)
        return {"url": figeon.binding_url()}

    @app.post("/api/social/figeon/connect")
    @handle
    def figeon_connect():
        if not protected_transport():
            return error("请通过 HTTPS 或本机工作台连接飞鸽。", 403)
        return {"accounts": figeon.connect(request.form.get("token", "").strip())}

    @app.post("/api/social/figeon/sync")
    @handle
    def figeon_sync():
        return {"count": figeon.sync()}

    @app.post("/api/social/versions/<int:version_id>/figeon-schedule")
    @handle
    def figeon_schedule(version_id):
        data = request.form
        return {"count": figeon.schedule(version_id, data.getlist("platforms"), data.get("content_key", ""),
                                         data.get("media", ""), data.get("visibility", ""), data.get("run_at", ""))}

    @app.post("/api/social/figeon/release")
    @handle
    def figeon_release():
        data = request.form
        return figeon.release(data.get("draft_id", type=int), data.get("request_key", ""), data.get("title", ""),
                              data.get("body", ""), selected_ids("news_ids"), data.getlist("platforms"),
                              data.get("media", ""), data.get("visibility", "unlisted"),
                              data.get("run_at") or social.utc_stamp(), asset_id=data.get("asset_id", type=int),
                              keep_image=data.get("keep_image") == "1")

    @app.get("/api/social/figeon/scheduled")
    def figeon_scheduled():
        return {"items": figeon.scheduled(), "platforms": figeon.PLATFORMS}

    @app.post("/api/social/figeon/batches/<int:batch_id>/cancel")
    @handle
    def figeon_cancel(batch_id):
        figeon.cancel(batch_id)
        return {"ok": True}

    @app.get("/api/social/figeon/posts/<int:remote>/<platform>/comments")
    @handle
    def figeon_comments(remote, platform):
        connected, _, _ = figeon.comment_context(remote, platform)
        return {"items": social.scored_comments("figeon:" + platform, "figeon-" + str(connected["revision"]), str(remote))}

    @app.post("/api/social/figeon/posts/<int:remote>/<platform>/sync")
    @handle
    def figeon_comment_sync(remote, platform):
        return figeon.sync_comments(remote, platform)

    @app.post("/api/social/figeon/posts/<int:remote>/<platform>/promote/<int:comment_id>")
    @handle
    def figeon_promote(remote, platform, comment_id):
        connected, post, _ = figeon.comment_context(remote, platform)
        return social.promote_comment(comment_id, "figeon:" + platform, "figeon-" + str(connected["revision"]),
                                      str(remote), post["title"], "飞鸽 " + figeon.PLATFORMS[platform])

    @app.get("/api/social/verification")
    def social_verification_status():
        active = verification.session(current_user()["id"])
        view = active.public() if active else {"state": "idle", "message": "", "image_ready": False}
        return {**view, **verification.login_summary()}

    @app.post("/api/social/verification/credentials")
    @handle
    def social_verification_credentials():
        if not protected_transport():
            return error("请通过 HTTPS 或本机工作台保存帐号。", 403)
        verification.save_login(request.form.get("username", ""), request.form.get("password", ""))
        verification.discard(current_user()["id"])
        return verification.login_summary()

    @app.post("/api/social/verification/start")
    @handle
    def social_verification_start():
        if not protected_transport():
            return error("请通过 HTTPS 或本机工作台打开人工验证。", 403)
        return verification.start(current_user()["id"])

    @app.get("/api/social/verification/image")
    @handle
    def social_verification_image():
        active = verification.session(current_user()["id"])
        if not active:
            abort(404)
        return Response(active.image(), mimetype="image/png", headers={"X-Content-Type-Options": "nosniff"})

    @app.post("/api/social/verification/submit")
    @handle
    def social_verification_submit():
        if not protected_transport():
            return error("请通过 HTTPS 或本机工作台提交人工验证。", 403)
        active = verification.session(current_user()["id"])
        if not active:
            return error("请先打开验证码会话。")
        return active.submit(request.form.get("code", "").strip())

    @app.cli.command("social-worker")
    @click.option("--once", is_flag=True, help="处理一轮到期发布和评论同步。")
    def social_worker(once):
        while True:
            with app.app_context():
                figeon_count = figeon.run_due()
                figeon.refresh_due(force=bool(figeon_count))
            if once:
                click.echo(f"飞鸽已提交 {figeon_count} 批。")
                return
            sleep(15)

    figeon.start_keepalive(app)
