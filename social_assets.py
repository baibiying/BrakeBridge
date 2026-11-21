from hashlib import sha256

from storage import get_db


def catalog():
    return [dict(row) for row in get_db().execute(
        "SELECT id,title,category,model FROM social_assets ORDER BY category, title")]


def get(asset_id):
    return get_db().execute("SELECT * FROM social_assets WHERE id=?", (asset_id,)).fetchone()


def attachment(asset_id):
    if asset_id is None:
        return "", None
    asset = get(asset_id)
    if asset is None:
        raise ValueError("该企业图片已不可用，请重新选择。")
    return "image/jpeg", asset["image"]


def store(title, category, model, image):
    if not image.startswith(b"\xff\xd8\xff") or len(image) > 4 * 1024 * 1024:
        raise ValueError("企业图片需为 4 MB 以内的 JPEG。")
    get_db().execute("""INSERT INTO social_assets(digest,title,category,model,image) VALUES(?,?,?,?,?)
                        ON CONFLICT(digest) DO UPDATE SET title=excluded.title,category=excluded.category,
                        model=excluded.model""", (sha256(image).hexdigest(), title, category, model, image))
