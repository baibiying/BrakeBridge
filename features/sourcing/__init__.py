from flask import Blueprint

sourcing_bp = Blueprint("sourcing", __name__)


@sourcing_bp.get("/sourcing")
def sourcing_home():
    from flask import render_template

    return render_template("base.html", title="拓客", active="sourcing")
