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
        body = json.loads(self.rfile.read(length))
        _Handler.received.append(body)
        if self.path == "/api/pull":
            self._send({"status": "success"})
        elif self.path == "/api/embed":
            n = len(body["input"])
            self._send({"embeddings": [[float(i), 1.0] for i in range(n)] if body["model"] != "bad" else []})
        else:
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


def test_installed_check_is_exact(server):
    # qwen2.5:3b しか無いとき、同じ系列の 7b を「インストール済み」と誤判定しない
    ok, msg = OllamaClient(server, "qwen2.5:7b").available()
    assert not ok


def test_auto_selects_best_installed_model(server):
    c = OllamaClient(server, "auto", ram_gb=16)
    ok, msg = c.available()
    assert ok and "qwen2.5:3b" in msg      # 16GB 機でも、入っている中で最良のものを使う
    c.generate_json("p")
    assert _Handler.received[-1]["model"] == "qwen2.5:3b"
    small = OllamaClient(server, "auto", ram_gb=2)
    ok, msg = small.available()
    assert not ok and "推奨" in msg


def test_pull_and_qwen3_think_disabled(server):
    c = OllamaClient(server, "qwen3:8b")
    c.pull("qwen3:8b")
    assert _Handler.received[-1] == {"model": "qwen3:8b", "stream": False}
    _Handler.response_text = "<think>考え中</think>" + json.dumps({"category_id": None, "confidence": 0.2})
    try:
        out = c.generate_json("p")
    finally:
        _Handler.response_text = json.dumps({"category_id": None, "confidence": 0.1, "reason": "test"})
    assert out["confidence"] == 0.2 and _Handler.received[-1]["think"] is False


def test_embed_in_batches_and_validates(server):
    c = OllamaClient(server, "qwen3:8b")
    vecs = c.embed([f"t{i}" for i in range(5)], "qwen3-embedding:0.6b", batch=2)
    assert len(vecs) == 5
    reqs = [r for r in _Handler.received if "input" in r]
    assert [len(r["input"]) for r in reqs] == [2, 2, 1]
    with pytest.raises(OllamaError):
        c.embed(["x"], "bad")
    assert c.has_model("qwen2.5:3b") and not c.has_model("qwen3-embedding:0.6b")
