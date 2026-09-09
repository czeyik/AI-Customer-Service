from html.parser import HTMLParser
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree


MAX_PAGE_BYTES = 2_000_000
MAX_SITEMAP_URLS = 100
ALLOWED_HOST = "duducar.co"
IGNORED_TAGS = {"script", "style", "nav", "footer", "form"}
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "source",
    "track",
    "wbr",
}


class ContentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.language = "en"
        self.parts: list[str] = []
        self._capture: str | None = None
        self._buffer: list[str] = []
        self._ignored_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "html" and attributes.get("lang", "").lower().startswith("ms"):
            self.language = "ms"
        elif tag == "html" and attributes.get("lang", "").lower().startswith("zh"):
            self.language = "zh"
        if self._ignored_stack:
            if tag not in VOID_TAGS:
                self._ignored_stack.append(tag)
            return
        if tag in IGNORED_TAGS or attributes.get("data-pagefind-ignore") == "all":
            self._ignored_stack.append(tag)
            return
        if tag in {"title", "h1", "h2", "h3", "h4", "h5", "h6", "p", "li"}:
            self._capture = tag
            self._buffer = []

    def handle_endtag(self, tag: str) -> None:
        if self._ignored_stack:
            if tag == self._ignored_stack[-1]:
                self._ignored_stack.pop()
            return
        if tag != self._capture:
            return
        value = " ".join("".join(self._buffer).split())
        if value:
            if tag == "title":
                self.title = value
            else:
                self.parts.append(value)
        self._capture = None
        self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._capture and not self._ignored_stack:
            self._buffer.append(data)


def _read_url(url: str) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST:
        raise ValueError("website knowledge URLs must use https://duducar.co")
    request = Request(url, headers={"User-Agent": "DUDU-Knowledge-Draft/1.0"})
    with urlopen(request, timeout=10) as response:
        final_url = urlparse(response.geturl())
        if final_url.scheme != "https" or final_url.hostname != ALLOWED_HOST:
            raise ValueError("website knowledge redirects must stay on https://duducar.co")
        data = response.read(MAX_PAGE_BYTES + 1)
    if len(data) > MAX_PAGE_BYTES:
        raise ValueError("website page exceeds the 2 MB extraction limit")
    return data


def sitemap_urls(sitemap_url: str = "https://duducar.co/sitemap.xml") -> list[str]:
    root = ElementTree.fromstring(_read_url(sitemap_url))
    urls = []
    for element in root.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc"):
        url = (element.text or "").strip()
        if url and url not in urls:
            parsed = urlparse(url)
            if parsed.scheme == "https" and parsed.hostname == ALLOWED_HOST:
                urls.append(url)
        if len(urls) >= MAX_SITEMAP_URLS:
            break
    return urls


def extract_page(url: str, chunk_chars: int = 1600) -> dict[str, str | list[str]]:
    parser = ContentParser()
    parser.feed(_read_url(url).decode("utf-8", errors="replace"))
    chunks: list[str] = []
    current = ""
    for part in parser.parts:
        if current and len(current) + len(part) + 1 > chunk_chars:
            chunks.append(current)
            current = part
        else:
            current = f"{current}\n{part}".strip()
    if current:
        chunks.append(current)
    if not parser.title or not chunks:
        raise ValueError(f"no useful page content found at {url}")
    return {"title": parser.title, "language": parser.language, "chunks": chunks}
