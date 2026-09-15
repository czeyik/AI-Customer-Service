from html.parser import HTMLParser

from app.config import get_settings
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_PAGE_BYTES = 2_000_000
ALLOWED_HOST = "duducar.co"
IGNORED_TAGS = {"script", "style", "nav", "footer", "form", "noscript", "svg"}
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
        self._blocks: list[str] = []

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
            if tag not in VOID_TAGS:
                self._ignored_stack.append(tag)
            return
        if tag == "title":
            self._capture = "title"
            self._buffer = []
            self._blocks = [tag]
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "tr", "table"}:
            if not self._blocks:
                self._flush()
                self._capture = "body"
            else:
                self._buffer.append(" ")
            self._blocks.append(tag)
        elif tag in {"td", "th", "br"}:
            self._buffer.append(" | " if tag in {"td", "th"} else " ")
        elif tag == "a" and attributes.get("href"):
            href = urljoin("https://duducar.co", attributes["href"])
            if urlparse(href).scheme == "https":
                self._buffer.append(f" [{href}] ")

    def _flush(self):
        value = " ".join("".join(self._buffer).split())
        if value:
            if self._capture == "title":
                self.title = value
            else:
                self.parts.append(value)
        self._buffer = []

    def handle_endtag(self, tag):
        if self._ignored_stack:
            if tag == self._ignored_stack[-1]:
                self._ignored_stack.pop()
            return
        if self._blocks and tag == self._blocks[-1]:
            self._blocks.pop()
            if not self._blocks:
                self._flush()
                self._capture = "body"
        elif not self._blocks and tag in {"div", "section"}:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._capture and not self._ignored_stack:
            self._buffer.append(data)


def _validate_url(url: str, message: str = "website knowledge URLs") -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST or parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise ValueError(f"{message} must stay on https://duducar.co")


class SameHostRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        newurl = urljoin(req.full_url, newurl)
        _validate_url(newurl, "website knowledge redirects")
        if req.full_url in get_settings().website_knowledge_urls and newurl not in get_settings().website_knowledge_urls:
            raise ValueError("website redirect is outside the selected URL allowlist")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read_url(url: str) -> bytes:
    _validate_url(url)
    request = Request(url, headers={"User-Agent": "DUDU-Knowledge-Draft/1.0"})
    with build_opener(SameHostRedirectHandler()).open(request, timeout=10) as response:
        _validate_url(response.geturl(), "website knowledge redirects")
        data = response.read(MAX_PAGE_BYTES + 1)
    if len(data) > MAX_PAGE_BYTES:
        raise ValueError("website page exceeds the 2 MB extraction limit")
    return data


def extract_page(url: str, chunk_chars: int = 1600) -> dict[str, str | list[str]]:
    _validate_url(url)
    if url not in get_settings().website_knowledge_urls:
        raise ValueError("website URL is not in the CCO-selected allowlist")
    if not 200 <= chunk_chars <= 4000:
        raise ValueError("chunk_chars must be between 200 and 4000")
    parser = ContentParser()
    parser.feed(_read_url(url).decode("utf-8", errors="replace"))
    parser._flush()
    chunks: list[str] = []
    current = ""
    for part in parser.parts:
        if len(part) > 4000:
            raise ValueError("a source block exceeds 4000 characters; review its boundaries before staging")
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
