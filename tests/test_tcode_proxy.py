import base64
import hashlib
import socket
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from restim_tcode_proxy.proxy import ChannelConfig, ProxyService
from restim_tcode_proxy.ws import (
    OP_PING,
    OP_PONG,
    OP_TEXT,
    accept_key,
    encode_frame,
    encode_text,
    parse_http_headers,
    try_decode_frame,
)


def _fake_restim(port_box: list[int], received: list[str], ready: threading.Event,
                 pongs: list[bytes] | None = None, send_ping: bool = False):
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port_box.append(server.getsockname()[1])
    ready.set()
    conn, _ = server.accept()
    request = b""
    while b"\r\n\r\n" not in request:
        request += conn.recv(4096)
    _, headers = parse_http_headers(request)
    key = headers["sec-websocket-key"]
    accept = accept_key(key)
    conn.sendall(
        ("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
         "Connection: Upgrade\r\n"
         f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode("ascii")
    )
    if send_ping:
        # Give the proxy a moment to enter relay, then ping as Restim would.
        time.sleep(0.05)
        conn.sendall(encode_frame(OP_PING, b"restim-ping", mask=False))
    buffer = bytearray()
    deadline = time.time() + 3.0
    while time.time() < deadline:
        conn.settimeout(0.2)
        try:
            chunk = conn.recv(4096)
        except socket.timeout:
            if received and (not send_ping or pongs):
                break
            continue
        if not chunk:
            break
        buffer.extend(chunk)
        while True:
            frame, consumed = try_decode_frame(buffer)
            if frame is None:
                break
            del buffer[:consumed]
            if frame.opcode == OP_TEXT:
                received.append(frame.payload.decode())
                deadline = time.time() + 0.5
            elif frame.opcode == OP_PONG and pongs is not None:
                pongs.append(frame.payload)
                deadline = time.time() + 0.5
    conn.close()
    server.close()


class WsTests(unittest.TestCase):
    def test_masked_roundtrip(self):
        raw = encode_text("L05000 V07000", mask=True)
        frame, consumed = try_decode_frame(bytearray(raw))
        self.assertEqual(consumed, len(raw))
        self.assertEqual(frame.opcode, OP_TEXT)
        self.assertEqual(frame.payload.decode(), "L05000 V07000")

    def test_accept_key_matches_rfc(self):
        key = "dGhlIHNhbXBsZSBub25jZQ=="
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
        ).decode()
        self.assertEqual(accept_key(key), expected)


class ProxyTests(unittest.TestCase):
    def test_proxy_forwards_and_always_logs(self):
        upstream_port: list[int] = []
        received: list[str] = []
        ready = threading.Event()
        threading.Thread(
            target=_fake_restim, args=(upstream_port, received, ready), daemon=True
        ).start()
        self.assertTrue(ready.wait(2.0))

        listen = socket.socket()
        listen.bind(("127.0.0.1", 0))
        listen_port = listen.getsockname()[1]
        listen.close()

        with TemporaryDirectory() as tmp:
            service = ProxyService(
                [ChannelConfig(
                    "Primary", "127.0.0.1", listen_port,
                    "127.0.0.1", upstream_port[0], True)],
                log_dir=Path(tmp),
            )
            log_path = service.start()
            try:
                # Vector-like client
                client = socket.create_connection(("127.0.0.1", listen_port), timeout=2)
                key = base64.b64encode(b"0123456789abcdef").decode()
                client.sendall(
                    (f"GET /tcode HTTP/1.1\r\nHost: 127.0.0.1:{listen_port}\r\n"
                     "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                     f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
                     ).encode("ascii")
                )
                response = b""
                while b"\r\n\r\n" not in response:
                    response += client.recv(4096)
                self.assertTrue(response.startswith(b"HTTP/1.1 101"))
                payload = "L01000 L12000 V03000 C04000"
                client.sendall(encode_text(payload, mask=True))
                deadline = time.time() + 3.0
                while time.time() < deadline and not received:
                    time.sleep(0.05)
                client.close()
                self.assertEqual(received, [payload])
                text = log_path.read_text(encoding="utf-8")
                self.assertIn("Primary", text)
                self.assertIn("IN", text)
                self.assertIn(payload, text)
            finally:
                service.stop()

    def test_proxy_answers_restim_ping_locally(self):
        upstream_port: list[int] = []
        received: list[str] = []
        pongs: list[bytes] = []
        ready = threading.Event()
        threading.Thread(
            target=_fake_restim,
            args=(upstream_port, received, ready, pongs, True),
            daemon=True,
        ).start()
        self.assertTrue(ready.wait(2.0))

        listen = socket.socket()
        listen.bind(("127.0.0.1", 0))
        listen_port = listen.getsockname()[1]
        listen.close()

        with TemporaryDirectory() as tmp:
            service = ProxyService(
                [ChannelConfig(
                    "Primary", "127.0.0.1", listen_port,
                    "127.0.0.1", upstream_port[0], True)],
                log_dir=Path(tmp),
            )
            service.start()
            try:
                client = socket.create_connection(("127.0.0.1", listen_port), timeout=2)
                key = base64.b64encode(b"pingpongkey12345").decode()
                client.sendall(
                    (f"GET /tcode HTTP/1.1\r\nHost: 127.0.0.1:{listen_port}\r\n"
                     "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                     f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
                     ).encode("ascii")
                )
                response = b""
                while b"\r\n\r\n" not in response:
                    response += client.recv(4096)
                self.assertTrue(response.startswith(b"HTTP/1.1 101"))
                deadline = time.time() + 3.0
                while time.time() < deadline and not pongs:
                    time.sleep(0.05)
                # Vector must not receive the Restim ping on its socket.
                client.settimeout(0.2)
                try:
                    leaked = client.recv(64)
                except socket.timeout:
                    leaked = b""
                client.close()
                self.assertEqual(pongs, [b"restim-ping"])
                self.assertEqual(leaked, b"")
            finally:
                service.stop()


if __name__ == "__main__":
    unittest.main()
