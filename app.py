from flask import Flask, render_template
import importlib

app = Flask(__name__)


@app.get("/")
def home():
    return render_template("base.html", title="BrakeBridge AI", active="home")


for module, name in [
    ("features.sourcing", "sourcing_bp"),
    ("features.inquiry", "inquiry_bp"),
    ("features.fitment", "fitment_bp"),
]:
    try:
        mod = importlib.import_module(module)
        app.register_blueprint(getattr(mod, name))
    except Exception as e:
        print("skip", module, e)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
