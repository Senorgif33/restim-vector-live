"""Tests for the embedded Vector control API (stdlib HTTP + WS)."""

from __future__ import annotations

import json
import socket
import time
import unittest
import urllib.error
import urllib.request

from vector1a.control_api import (
    ACTIONS,
    DESKTOP_ONLY_FIELDS,
    ControlApiServer,
    accept_key,
    encode_text,
    try_decode_frame,
    writable_fields,
)


class FakeBackend:
    def __init__(self) -> None:
        self.state = {
            "version": "test",
            "settings": {"volume": 0.7, "dynamic_volume": True},
            "status": {"mfp_status": "Disconnected"},
            "diagnostics": {"raw_l0": 0.5},
            "meters": {"e1": 0.5, "e2": 0.5, "e3": 0.5, "e4": 0.5},
        }
        self.actions: list[str] = []

    def control_schema(self) -> dict:
        return {
            "version": 1,
            "writable": ["volume", "dynamic_volume"],
            "actions": list(ACTIONS),
        }

    def control_state(self) -> dict:
        return self.state

    def control_patch(self, patch: dict) -> dict:
        settings = patch.get("settings", patch)
        applied = []
        for key, value in settings.items():
            if key in ("volume", "dynamic_volume"):
                self.state["settings"][key] = value
                applied.append(key)
        return {"ok": True, "applied": applied, "unknown": [], "state": self.state}

    def control_action(self, name: str) -> dict:
        self.actions.append(name)
        if name not in ACTIONS:
            return {"ok": False, "error": f"unknown action: {name}"}
        return {"ok": True, "action": name, "state": self.state}


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _http_json(method: str, url: str, body: dict | None = None) -> tuple[int, dict]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if body is not None else {})
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


class WritableFieldsTests(unittest.TestCase):
    def test_excludes_desktop_only_and_adds_meta(self):
        fields = writable_fields((
            "volume", "mfp_launch_target", "events_file_path", "four_phase_host",
        ))
        self.assertIn("volume", fields)
        self.assertIn("control_api_enabled", fields)
        self.assertIn("control_api_host", fields)
        self.assertIn("control_api_port", fields)
        for name in DESKTOP_ONLY_FIELDS:
            self.assertNotIn(name, fields)


class ControlApiHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = FakeBackend()
        self.port = _free_port()
        self.server = ControlApiServer(self.backend, "127.0.0.1", self.port, stream_hz=20.0)
        self.server.start()
        self.base = f"http://127.0.0.1:{self.port}"
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                _http_json("GET", f"{self.base}/v1/state")
                break
            except OSError:
                time.sleep(0.02)

    def tearDown(self) -> None:
        self.server.stop()

    def test_get_state_and_schema(self):
        status, state = _http_json("GET", f"{self.base}/v1/state")
        self.assertEqual(status, 200)
        self.assertEqual(state["settings"]["volume"], 0.7)
        status, schema = _http_json("GET", f"{self.base}/v1/schema")
        self.assertEqual(status, 200)
        self.assertIn("volume", schema["writable"])
        self.assertIn("neutral", schema["actions"])

    def test_cors_preflight(self):
        request = urllib.request.Request(
            f"{self.base}/v1/state", method="OPTIONS")
        with urllib.request.urlopen(request, timeout=3) as response:
            self.assertEqual(response.status, 204)
            self.assertEqual(response.headers.get("Access-Control-Allow-Origin"), "*")

    def test_post_state_patch(self):
        status, body = _http_json("POST", f"{self.base}/v1/state", {"volume": 0.42})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertIn("volume", body["applied"])
        self.assertEqual(self.backend.state["settings"]["volume"], 0.42)

    def test_actions(self):
        status, body = _http_json("POST", f"{self.base}/v1/actions/neutral")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(self.backend.actions, ["neutral"])
        status, body = _http_json("POST", f"{self.base}/v1/actions/not-a-thing")
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_websocket_stream_sends_snapshot(self):
        key = "dGhlIHNhbXBsZSBub25jZQ=="
        request = (
            f"GET /v1/stream HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        ).encode("ascii")
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=3)
        try:
            sock.sendall(request)
            response = b""
            while b"\r\n\r\n" not in response:
                chunk = sock.recv(4096)
                self.assertTrue(chunk)
                response += chunk
            self.assertIn(b"101", response.split(b"\r\n", 1)[0])
            self.assertIn(accept_key(key).encode("ascii"), response)
            buffer = bytearray(response.split(b"\r\n\r\n", 1)[1])
            deadline = time.monotonic() + 2.0
            message = None
            while time.monotonic() < deadline and message is None:
                frame, consumed = try_decode_frame(buffer)
                if frame is None:
                    sock.settimeout(0.2)
                    try:
                        buffer.extend(sock.recv(4096))
                    except TimeoutError:
                        continue
                    continue
                del buffer[:consumed]
                opcode, payload = frame
                if opcode == 0x1:
                    message = json.loads(payload.decode("utf-8"))
            self.assertIsNotNone(message)
            self.assertEqual(message["type"], "snapshot")
            self.assertEqual(message["state"]["settings"]["volume"], 0.7)
        finally:
            sock.close()


class FrameHelperTests(unittest.TestCase):
    def test_round_trip_text_frame_unmasked(self):
        raw = encode_text('{"ok":true}')
        buffer = bytearray(raw)
        frame, consumed = try_decode_frame(buffer)
        self.assertIsNotNone(frame)
        opcode, payload = frame
        self.assertEqual(opcode, 0x1)
        self.assertEqual(payload, b'{"ok":true}')
        self.assertEqual(consumed, len(raw))


if __name__ == "__main__":
    unittest.main()
