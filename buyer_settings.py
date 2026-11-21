import json

from scoring import BUYER_BANDS, BUYER_DEFAULTS, BUYER_DIMENSIONS
from storage import get_db


def weights(key="buyer_weights", defaults=BUYER_DEFAULTS):
    row = get_db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else defaults.copy()


def describe():
    return {"weights": weights(), "defaults": BUYER_DEFAULTS, "dimensions": BUYER_DIMENSIONS, "bands": BUYER_BANDS}


def validate(values, defaults=BUYER_DEFAULTS):
    if (not isinstance(values, dict) or values.keys() != defaults.keys()
            or any(type(value) is not int or not 0 <= value <= 100 for value in values.values())
            or sum(values.values()) != 100):
        raise ValueError("权重须为 0 至 100 的整数，合计 100%。")
    return values


def save(values, key="buyer_weights"):
    get_db().execute("""INSERT INTO settings(key,value) VALUES(?,?)
                        ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (key, json.dumps(values)))
