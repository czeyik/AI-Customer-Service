import re

CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")
MALAY_HINTS = {
    "saya",
    "anda",
    "terima",
    "kasih",
    "pemandu",
    "penumpang",
    "tambang",
    "bayaran",
    "akaun",
    "perjalanan",
    "kereta",
    "batal",
    "promosi",
}


def detect_language(text: str) -> str:
    lowered = text.lower()
    if CHINESE_RE.search(text):
        return "zh"
    tokens = set(re.findall(r"[a-zA-Z]+", lowered))
    if tokens & MALAY_HINTS:
        return "ms"
    return "en"


def language_name(language: str) -> str:
    return {"en": "English", "ms": "Bahasa Malaysia", "zh": "Simplified Chinese"}.get(
        language, "English"
    )

