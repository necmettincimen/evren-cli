"""
Web fetch tool backend for EVREN CLI.

Fetches an http/https URL with urllib (stdlib only), converts HTML into
readable plain text (script/style/nav dropped, headings and links kept) and
returns JSON / plain-text bodies verbatim. Enforces a size cap while reading,
a request timeout, and a final character truncation so a single fetch cannot
blow up the conversation context. All failures return short Turkish error
strings instead of raising, mirroring the other tool executors.
"""

from __future__ import annotations

import html
import re
import urllib.error
import urllib.request

DEFAULT_TIMEOUT = 15
MAX_TIMEOUT = 60
DEFAULT_MAX_BYTES = 200_000
# Every tool result stays in the history and is re-sent on later steps, so a
# docs page is capped like other tool output (~3.5k tokens).
DEFAULT_MAX_CHARS = 12_000

_USER_AGENT = "Mozilla/5.0 (compatible; EVREN-CLI/2.0; +web_fetch)"

# Tags whose entire content is dropped (never useful as readable text).
_DROP_TAGS = ("script", "style", "nav", "header", "footer", "iframe", "noscript", "svg")

# Block-level tags that end a text block (rendered as a line break).
_BLOCK_TAGS = (
    "p", "div", "section", "article", "main", "aside", "ul", "ol", "table",
    "tr", "blockquote", "pre", "figure", "figcaption", "form", "fieldset",
    "dl", "dt", "dd", "address", "hr",
)

_TAG_RE = re.compile(r"<[^>]+>")

# Binary payloads decoded as text only fill the context with mojibake.
_BINARY_TYPES = (
    "application/pdf", "application/zip", "application/octet-stream", "image/", "audio/",
    "video/", "font/", "application/x-",
)


def _ssl_context():
    """TLS settings shared with the API client (EVREN_CA_BUNDLE / EVREN_SSL_VERIFY).

    Without it the tool ignored the corporate CA bundle and failed on networks
    where every other EVREN request worked.
    """
    import ssl

    try:
        from src.config import get_ssl_verify

        verify = get_ssl_verify()
    except Exception:
        verify = True
    if verify is False:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    if isinstance(verify, str) and verify:
        return ssl.create_default_context(cafile=verify)
    return ssl.create_default_context()
_WS_RE = re.compile(r"[ \t\f\v]+")
_MANY_BLANK_RE = re.compile(r"\n{3,}")


def _validate_url(url: str) -> str | None:
    """Returns an error message if the URL is not a usable http(s) URL."""
    if not url or not isinstance(url, str) or not url.strip():
        return "HATA: web_fetch için boş URL."
    url = url.strip()
    lowered = url.lower()
    if not (lowered.startswith("http://") or lowered.startswith("https://")):
        return "HATA: web_fetch yalnızca http/https URL'lerini destekler."
    return None


def _attr(attrs: str, name: str) -> str:
    """Extracts a single attribute value from a raw tag attribute string."""
    m = re.search(rf'\b{name}\s*=\s*"([^"]*)"', attrs, re.IGNORECASE)
    if not m:
        m = re.search(rf"\b{name}\s*=\s*'([^']*)'", attrs, re.IGNORECASE)
    if not m:
        # Minified HTML often drops the quotes: href=/docs/x
        m = re.search(rf"\b{name}\s*=\s*([^\s\"'>]+)", attrs, re.IGNORECASE)
    return html.unescape(m.group(1)) if m else ""


def _html_to_text(text: str) -> str:
    """Converts an HTML document into readable plain text.

    - script/style/nav/header/footer/iframe/noscript/svg content is dropped,
    - <h1..h6> become Markdown-style headings ("# ", "## ", ...),
    - <a href="..."> becomes "text (url)",
    - <li> becomes "- ", <br> becomes a line break,
    - block-level tags become line breaks, everything else is inline.
    """
    # The page title lives in <head>, which the body extraction drops.
    title_match = re.search(r"<title[^>]*>(.*?)</title\s*>", text, re.IGNORECASE | re.DOTALL)
    title = html.unescape(_TAG_RE.sub("", title_match.group(1))).strip() if title_match else ""

    # Normalize to a single document body when possible.
    m = re.search(r"<body[^>]*>(.*)</body>", text, re.IGNORECASE | re.DOTALL)
    if m:
        text = m.group(1)

    # Drop content of noise tags (non-greedy, tolerates attributes/newlines).
    for tag in _DROP_TAGS:
        text = re.sub(
            rf"<{tag}\b[^>]*>.*?</{tag}\s*>", "", text, flags=re.IGNORECASE | re.DOTALL,
        )
    # Self-closing / unclosed noise tags: drop the tag itself.
    text = re.sub(
        r"</?(?:%s)\b[^>]*>" % "|".join(_DROP_TAGS), "", text, flags=re.IGNORECASE,
    )

    # Comments.
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)

    # Links: keep text and destination.
    def _link(match: re.Match) -> str:
        inner = _TAG_RE.sub("", match.group(2)).strip()
        href = _attr(match.group(1), "href").strip()
        if not inner:
            return ""
        return f"{inner} ({href})" if href else inner

    text = re.sub(
        r"<a\b([^>]*)>(.*?)</a\s*>", _link, text, flags=re.IGNORECASE | re.DOTALL,
    )

    # Headings -> "# ".."###### " prefixes.
    def _heading(match: re.Match) -> str:
        level = min(6, max(1, int(match.group(1))))
        inner = _TAG_RE.sub("", match.group(2)).strip()
        return f"\n{'#' * level} {inner}\n" if inner else ""

    text = re.sub(
        r"<h([1-6])\b[^>]*>(.*?)</h\1\s*>", _heading, text, flags=re.IGNORECASE | re.DOTALL,
    )

    # List items -> "- " bullets.
    text = re.sub(
        r"<li\b[^>]*>", "\n- ", text, flags=re.IGNORECASE,
    )
    text = re.sub(r"</li\s*>", "\n", text, flags=re.IGNORECASE)

    # Line breaks and blocks.
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    block_re = r"</?(?:%s)\b[^>]*>" % "|".join(_BLOCK_TAGS)
    text = re.sub(block_re, "\n", text, flags=re.IGNORECASE)

    # Remaining tags (spans, emphasis, images, ...) vanish.
    text = _TAG_RE.sub("", text)

    text = html.unescape(text)

    # Collapse whitespace: inline runs -> single space, many blank lines -> two.
    lines = [_WS_RE.sub(" ", line).strip() for line in text.splitlines()]
    text = "\n".join(lines)
    text = _MANY_BLANK_RE.sub("\n\n", text).strip()
    if title and not text.startswith(f"# {title}"):
        text = f"# {title}\n\n{text}" if text else f"# {title}"
    return text


def _truncate(text: str, max_chars: int) -> str:
    """Keeps the head of the text and notes how much was dropped."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    dropped = len(text) - max_chars
    return f"{text[:max_chars]}\n... [{dropped} karakter kırpıldı] ..."


def fetch_url(
    url: str,
    *,
    timeout: int | None = None,
    max_bytes: int | None = None,
    max_chars: int | None = None,
) -> str:
    """GETs `url` and returns readable text (HTML converted, JSON/plain as-is).

    Never raises: transport errors, timeouts and HTTP errors come back as short
    "HATA: ..." strings so the agent loop can react without crashing.
    """
    err = _validate_url(url)
    if err:
        return err
    url = url.strip()

    try:
        timeout = min(int(timeout) if timeout else DEFAULT_TIMEOUT, MAX_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    try:
        max_bytes = int(max_bytes) if max_bytes else DEFAULT_MAX_BYTES
    except (TypeError, ValueError):
        max_bytes = DEFAULT_MAX_BYTES
    try:
        max_chars = int(max_chars) if max_chars else DEFAULT_MAX_CHARS
    except (TypeError, ValueError):
        max_chars = DEFAULT_MAX_CHARS

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,text/plain;q=0.8,*/*;q=0.5",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout, context=_ssl_context()) as resp:
            content_type = (resp.headers.get("Content-Type") or "").lower()
            if any(content_type.startswith(kind) for kind in _BINARY_TYPES):
                return (
                    f"HATA: İkili içerik ({content_type.split(';')[0]}) metne çevrilemez ({url}). "
                    "Dosya gerekiyorsa run_command ile indir."
                )
            body = resp.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        return f"HATA: HTTP {exc.code} {exc.reason} ({url})"
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        return f"HATA: Bağlantı kurulamadı: {reason} ({url})"
    except TimeoutError:
        return f"HATA: Zaman aşımı ({timeout}s) ({url})"
    except Exception as exc:  # noqa: BLE001 - tool must never crash the loop
        return f"HATA: Beklenmeyen hata: {exc} ({url})"

    size_note = ""
    if len(body) > max_bytes:
        body = body[:max_bytes]
        size_note = f"\n\n[UYARI: Gövde {max_bytes} bayt sınırında kesildi.]"

    charset = "utf-8"
    m = re.search(r"charset=([\w\-]+)", content_type)
    if m:
        charset = m.group(1)
    try:
        text = body.decode(charset, errors="replace")
    except LookupError:
        text = body.decode("utf-8", errors="replace")

    if "json" in content_type or "text/plain" in content_type:
        result = text
    elif "html" in content_type or "<html" in text[:2000].lower() or "<body" in text[:2000].lower():
        result = _html_to_text(text)
    else:
        # Unknown/binary-ish payload: strip tags conservatively rather than
        # dumping raw bytes into the conversation.
        result = _TAG_RE.sub("", text)

    result = _truncate(result, max_chars)
    return result + size_note
