"""Tests for src.web_fetch — no real network access (urlopen is mocked)."""

import io
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from src import web_fetch
from src.web_fetch import fetch_url, _html_to_text, _truncate, _validate_url


class _FakeResponse(io.BytesIO):
    """Minimal context-manager stand-in for urllib's HTTP response."""

    def __init__(self, body: bytes, content_type: str = "text/html; charset=utf-8"):
        super().__init__(body)
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _patched_urlopen(resp):
    return patch.object(web_fetch.urllib.request, "urlopen", return_value=resp)


# --- URL validation ----------------------------------------------------------

def test_rejects_non_http_scheme():
    result = fetch_url("ftp://example.com/file")
    assert result.startswith("HATA")
    assert "http" in result






# --- HTML conversion ----------------------------------------------------------

_HTML = """<html><head><style>.a{color:red}</style><script>alert(1)</script></head>
<body><nav><a href="/x">Menu</a></nav>
<h1>Başlık</h1><p>Merhaba <b>dünya</b>.</p>
<ul><li>öğe bir</li><li>öğe iki</li></ul>
<a href="https://example.com/a">bağlantı</a>
<div>blok sonu</div></body></html>"""






def test_fetch_html_converts():
    resp = _FakeResponse(_HTML.encode("utf-8"), "text/html; charset=utf-8")
    with _patched_urlopen(resp):
        out = fetch_url("https://example.com/page")
    assert "# Başlık" in out
    assert "alert" not in out


# --- JSON / plain text passthrough ---------------------------------------------





# --- Size / truncation limits ---------------------------------------------------

def test_max_bytes_caps_body():
    body = b"x" * 5000
    resp = _FakeResponse(body, "text/plain")
    with _patched_urlopen(resp):
        out = fetch_url("https://example.com/big", max_bytes=1000, max_chars=100_000)
    assert "kesildi" in out
    assert len(out) < 5000






# --- Error handling --------------------------------------------------------------



def test_http_error_captured():
    err = urllib.error.HTTPError(
        "https://example.com/missing", 404, "Not Found", None, None,  # type: ignore[arg-type]
    )
    with patch.object(web_fetch.urllib.request, "urlopen", side_effect=err):
        out = fetch_url("https://example.com/missing")
    assert "HTTP 404" in out




def test_timeout_capped_at_max():
    """The timeout passed to urlopen must never exceed MAX_TIMEOUT."""
    resp = _FakeResponse(b"ok", "text/plain")
    with patch.object(web_fetch.urllib.request, "urlopen", return_value=resp) as m:
        fetch_url("https://example.com", timeout=9999)
    assert m.call_args.kwargs["timeout"] == web_fetch.MAX_TIMEOUT
