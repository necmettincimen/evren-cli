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


def test_rejects_empty_url():
    assert fetch_url("").startswith("HATA")
    assert fetch_url("   ").startswith("HATA")


def test_validate_url_accepts_https():
    assert _validate_url("https://example.com") is None


# --- HTML conversion ----------------------------------------------------------

_HTML = """<html><head><style>.a{color:red}</style><script>alert(1)</script></head>
<body><nav><a href="/x">Menu</a></nav>
<h1>Başlık</h1><p>Merhaba <b>dünya</b>.</p>
<ul><li>öğe bir</li><li>öğe iki</li></ul>
<a href="https://example.com/a">bağlantı</a>
<div>blok sonu</div></body></html>"""


def test_html_to_text_drops_script_style_nav():
    text = _html_to_text(_HTML)
    assert "alert" not in text
    assert "color:red" not in text
    assert "Menu" not in text  # nav dropped


def test_html_to_text_keeps_headings_links_lists():
    text = _html_to_text(_HTML)
    assert "# Başlık" in text
    assert "bağlantı (https://example.com/a)" in text
    assert "- öğe bir" in text
    assert "Merhaba dünya." in text


def test_fetch_html_converts():
    resp = _FakeResponse(_HTML.encode("utf-8"), "text/html; charset=utf-8")
    with _patched_urlopen(resp):
        out = fetch_url("https://example.com/page")
    assert "# Başlık" in out
    assert "alert" not in out


# --- JSON / plain text passthrough ---------------------------------------------

def test_json_returned_verbatim():
    payload = '{"a": 1, "b": "<not html>"}'
    resp = _FakeResponse(payload.encode("utf-8"), "application/json")
    with _patched_urlopen(resp):
        out = fetch_url("https://example.com/api")
    assert out == payload


def test_plain_text_returned_verbatim():
    payload = "satır 1\nsatır 2 <degismez>"
    resp = _FakeResponse(payload.encode("utf-8"), "text/plain; charset=utf-8")
    with _patched_urlopen(resp):
        out = fetch_url("https://example.com/robots.txt")
    assert out == payload


# --- Size / truncation limits ---------------------------------------------------

def test_max_bytes_caps_body():
    body = b"x" * 5000
    resp = _FakeResponse(body, "text/plain")
    with _patched_urlopen(resp):
        out = fetch_url("https://example.com/big", max_bytes=1000, max_chars=100_000)
    assert "kesildi" in out
    assert len(out) < 5000


def test_max_chars_truncates_output():
    payload = "a" * 3000
    resp = _FakeResponse(payload.encode("utf-8"), "text/plain")
    with _patched_urlopen(resp):
        out = fetch_url("https://example.com/long", max_chars=500)
    assert "karakter kırpıldı" in out
    assert out.startswith("a" * 500)


def test_truncate_noop_under_limit():
    assert _truncate("kısa", 100) == "kısa"


# --- Error handling --------------------------------------------------------------

def test_timeout_error_captured():
    with patch.object(web_fetch.urllib.request, "urlopen", side_effect=TimeoutError()):
        out = fetch_url("https://example.com/slow", timeout=1)
    assert out.startswith("HATA")
    assert "Zaman aşımı" in out


def test_http_error_captured():
    err = urllib.error.HTTPError(
        "https://example.com/missing", 404, "Not Found", None, None,  # type: ignore[arg-type]
    )
    with patch.object(web_fetch.urllib.request, "urlopen", side_effect=err):
        out = fetch_url("https://example.com/missing")
    assert "HTTP 404" in out


def test_url_error_captured():
    with patch.object(
        web_fetch.urllib.request, "urlopen",
        side_effect=urllib.error.URLError("no route to host"),
    ):
        out = fetch_url("https://example.com/down")
    assert "Bağlantı kurulamadı" in out


def test_timeout_capped_at_max():
    """The timeout passed to urlopen must never exceed MAX_TIMEOUT."""
    resp = _FakeResponse(b"ok", "text/plain")
    with patch.object(web_fetch.urllib.request, "urlopen", return_value=resp) as m:
        fetch_url("https://example.com", timeout=9999)
    assert m.call_args.kwargs["timeout"] == web_fetch.MAX_TIMEOUT


class HtmlPolishTest(unittest.TestCase):
    def test_title_and_unquoted_href_survive(self):
        from src.web_fetch import _html_to_text

        page = ("<html><head><title>Docs &amp; Guide</title></head><body>"
                "<p>Merhaba</p><a href=/kurulum>Kurulum</a></body></html>")
        text = _html_to_text(page)
        self.assertTrue(text.startswith("# Docs & Guide"))
        self.assertIn("Kurulum (/kurulum)", text)

    def test_binary_content_is_refused(self):
        from unittest import mock
        from src import web_fetch

        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        resp.headers = {"Content-Type": "application/pdf"}
        with mock.patch("src.web_fetch.urllib.request.urlopen", return_value=resp):
            out = web_fetch.fetch_url("https://example.test/a.pdf")
        self.assertTrue(out.startswith("HATA: İkili içerik"))
        resp.read.assert_not_called()
