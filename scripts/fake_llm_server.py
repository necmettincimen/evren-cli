"""LLM gerektirmeyen uyumluluk testleri için sahte, OpenAI uyumlu sunucu.

    python scripts/fake_llm_server.py [--port 0] [--port-file yol]

Gerçek modele bağlanmadan `evren --stdio` sürecini uçtan uca koşturmak için
yeterli yüzeyi sunar: GET /v1/models, GET /v1/quota, POST /v1/chat/completions
(akışlı ve akışsız). Davranış deterministiktir:

- Son kullanıcı mesajı "dosya:" ile başlıyorsa (ör. "dosya: notlar.txt") ilk
  yanıt o yola `write_file` araç çağrısıdır; araç sonucu geldikten sonra kısa
  bir metinle tur biter.
- Diğer her istek "Merhaba, ben sahte model." metniyle biter.

CI'da evren-vscode da bu dosyayı evren-cli'nin bir tag'inden alıp kullanır;
bu yüzden yalnız standart kütüphaneye dayanır.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MODEL = "fake-model"
REPLY = "Merhaba, ben sahte model."


def _last_user(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content")
            if isinstance(content, list):
                content = " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
            return str(content or "")
    return ""


def plan_reply(messages: list[dict]) -> dict:
    """Returns {"content": str} or {"tool_call": {...}} for the next assistant turn."""
    if messages and messages[-1].get("role") == "tool":
        return {"content": "Dosya yazıldı."}
    text = _last_user(messages).strip()
    marker = text.lower().find("dosya:")
    if marker != -1:
        rest = text[marker + len("dosya:"):].strip()
        path = rest.split()[0] if rest else "notlar.txt"
        args = {"path": path, "content": "sahte model içeriği\n"}
        return {"tool_call": {"id": "call_fake_1", "name": "write_file", "arguments": json.dumps(args)}}
    return {"content": REPLY}


def _chunk(delta: dict, finish: str | None = None) -> dict:
    return {"id": "chatcmpl-fake", "object": "chat.completion.chunk", "created": int(time.time()),
            "model": MODEL, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # noqa: D401 - stderr'i sessiz tut
        sys.stderr.write("fake-llm: " + (fmt % args) + "\n")

    def _json(self, status: int, body: dict, headers: dict | None = None) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")
        if path.endswith("/models"):
            return self._json(200, {"object": "list", "data": [{"id": MODEL, "object": "model"},
                                                               {"id": "fake-model-2", "object": "model"}]})
        if path.endswith("/quota"):
            return self._json(200, {"remaining_daily_tokens": 1000000, "daily_limit": 1000000},
                              {"x-evren-daily-remaining-tokens": "1000000"})
        return self._json(404, {"error": {"message": f"yok: {path}"}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": {"message": "bozuk JSON"}})
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._json(404, {"error": {"message": f"yok: {self.path}"}})
        reply = plan_reply(req.get("messages") or [])
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        if not req.get("stream"):
            message = {"role": "assistant", "content": reply.get("content", "")}
            finish = "stop"
            if "tool_call" in reply:
                tc = reply["tool_call"]
                message["tool_calls"] = [{"id": tc["id"], "type": "function",
                                          "function": {"name": tc["name"], "arguments": tc["arguments"]}}]
                finish = "tool_calls"
            return self._json(200, {"id": "chatcmpl-fake", "object": "chat.completion", "created": int(time.time()),
                                    "model": MODEL, "usage": usage,
                                    "choices": [{"index": 0, "message": message, "finish_reason": finish}]})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        chunks = [_chunk({"role": "assistant", "content": ""})]
        if "tool_call" in reply:
            tc = reply["tool_call"]
            chunks.append(_chunk({"tool_calls": [{"index": 0, "id": tc["id"], "type": "function",
                                                  "function": {"name": tc["name"], "arguments": tc["arguments"]}}]}))
            chunks.append(_chunk({}, "tool_calls"))
        else:
            for word in reply["content"].split(" "):
                chunks.append(_chunk({"content": word + " "}))
            chunks.append(_chunk({}, "stop"))
        last = chunks[-1]
        last["usage"] = usage
        for c in chunks:
            self.wfile.write(b"data: " + json.dumps(c, ensure_ascii=False).encode("utf-8") + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True


def serve(port: int = 0) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--port-file", help="seçilen portun yazılacağı dosya")
    args = ap.parse_args(argv)
    server = serve(args.port)
    port = server.server_address[1]
    if args.port_file:
        Path(args.port_file).write_text(str(port), encoding="utf-8")
    print(f"http://127.0.0.1:{port}/v1", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
