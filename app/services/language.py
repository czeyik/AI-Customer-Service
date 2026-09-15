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
    "ya",
    "tidak",
    "setuju",
    "nama",
    "emel",
}


def detect_language(text: str) -> str:
    lowered = text.lower()
    if CHINESE_RE.search(text):
        return "zh"
    tokens = set(re.findall(r"[a-zA-Z]+", lowered))
    if tokens & MALAY_HINTS:
        return "ms"
    return "en"


def selected_language(text: str) -> str | None:
    lowered = text.lower()
    selections = {
        "zh": ("请用中文", "说中文", "用简体中文", "speak chinese", "switch to chinese"),
        "ms": (
            "guna bahasa malaysia",
            "guna bahasa melayu",
            "cakap bahasa melayu",
            "speak malay",
            "switch to malay",
        ),
        "en": ("please use english", "speak english", "switch to english", "请用英语"),
    }
    return next(
        (language for language, phrases in selections.items() if any(p in lowered for p in phrases)),
        None,
    )


def is_language_selection(text: str) -> bool:
    return bool(re.fullmatch(
        r"(?i)\s*(?:please |sila )?(?:use english|speak (?:english|malay|chinese)|switch to (?:english|malay|chinese)|guna bahasa (?:malaysia|melayu)|cakap bahasa melayu|请用中文|说中文|用简体中文|请用英语)\s*[.!。]*\s*", text
    ))
