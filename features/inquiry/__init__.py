from flask import Blueprint

inquiry_bp = Blueprint("inquiry", __name__)


@inquiry_bp.get("/inquiry")
def inquiry_home():
    from flask import render_template

    return render_template("base.html", title="询盘", active="inquiry")
