"""OllamaClient の HTTP 通信をローカルの偽 Ollama サーバーで検証する。"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from classifier.ollama import OllamaClient, OllamaError


class _Handler(BaseHTTPRequestHandler):
    response_text = json.dumps({"category_id": None, "confidence": 0.1, "reason": "test"})
    received: list = []

    def log_message(self, *a):
        pass

    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/tags":
            self._send({"models": [{"name": "qwen2.5:3b"}]})

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        _Handler.received.append(json.loads(self.rfile.read(length)))
        self._send({"response": _Handler.response_text})


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    _Handler.received = []
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_available_and_generate(server):
    c = OllamaClient(server, "qwen2.5:3b", timeout=5)
    ok, msg = c.available()
    assert ok, msg
    out = c.generate_json("prompt")
    assert out["reason"] == "test"
    req = _Handler.received[0]
    assert req["format"] == "json" and req["stream"] is False and "データ" in req["system"]


def test_missing_model_and_bad_json(server):
    ok, msg = OllamaClient(server, "llama3:8b").available()
    assert not ok and "インストール" in msg
    _Handler.response_text = "これは JSON ではない"
    try:
        with pytest.raises(OllamaError):
            OllamaClient(server, "qwen2.5:3b").generate_json("p")
    finally:
        _Handler.response_text = json.dumps({"category_id": None, "confidence": 0.1, "reason": "test"})
