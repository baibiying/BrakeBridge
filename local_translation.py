import importlib.util
import json
import re
from functools import lru_cache
from pathlib import Path
from threading import Lock

from flask import current_app

MODEL_LOCK = Lock()
MODEL_LINE = re.compile(r"(?<=[.!?])\s+|(?<=;)\s+")
RFQ = re.compile(r"\bplease quote\b", re.I)


def location():
    return Path(current_app.config["LEADS_TRANSLATION_MODEL"])


def available():
    path = location()
    return ((path / "model" / "model.bin").is_file() and (path / "sentencepiece.model").is_file()
            and importlib.util.find_spec("ctranslate2") is not None
            and importlib.util.find_spec("sentencepiece") is not None)


@lru_cache(maxsize=1)
def load_model(path):
    from ctranslate2 import Translator
    from sentencepiece import SentencePieceProcessor
    metadata = path / "metadata.json"
    prefix = json.loads(metadata.read_text(encoding="utf-8")).get("target_prefix", "") if metadata.is_file() else ""
    return Translator(str(path / "model"), device="cpu", intra_threads=2), SentencePieceProcessor(
        model_file=str(path / "sentencepiece.model")), prefix


def split_line(line):
    for piece in MODEL_LINE.split(line):
        while len(piece) > 600:
            boundary = piece.rfind(" ", 300, 600)
            boundary = boundary + 1 if boundary != -1 else 600
            yield piece[:boundary]
            piece = piece[boundary:]
        if piece:
            yield piece


def clean_translation(text, source):
    text = text.replace("▁", " ").strip()
    if re.search(r"\bbrake linings?\b", source, re.I):
        text = re.sub(r"制动衬[里垫]|刹车衬片?", "制动衬片", text)
    if re.search(r"\bbrake pads?\b", source, re.I):
        text = re.sub(r"盘式?制动垫|刹车台|制动垫", lambda match:
                      "盘式刹车片" if match[0].startswith("盘") else "刹车片", text)
    return text


def translate(text):
    if not available():
        raise ValueError("服务器未安装英译中语言包。")
    lines = text.splitlines()
    try:
        batches = []
        line_indexes = []
        for idx, line in enumerate(lines):
            if not line.strip():
                continue
            pieces = list(split_line(RFQ.sub("Please provide a price for", line)))
            batches.extend(pieces)
            line_indexes.extend([idx] * len(pieces))
        with MODEL_LOCK:
            translator, tokenizer, prefix = load_model(location())
            encoded = tokenizer.encode(batches, out_type=str)
            translated = translator.translate_batch(encoded, replace_unknowns=True, beam_size=4,
                                                    max_batch_size=32, max_decoding_length=480,
                                                    target_prefix=[[prefix]] * len(encoded) if prefix else None)
            decoded = [clean_translation(tokenizer.decode(item.hypotheses[0]).removeprefix(prefix), source)
                       for item, source in zip(translated, batches)]
    except (ImportError, OSError, RuntimeError, ValueError) as error:
        raise ValueError("服务器离线翻译暂不可用。") from error
    output = [[] for _ in lines]
    for idx, item in zip(line_indexes, decoded):
        output[idx].append(item)
    return "\n".join(" ".join(values) for values in output)
