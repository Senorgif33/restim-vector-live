"""Minimal RFC 6455 helpers for a terminate/reoriginate /tcode proxy."""

from __future__ import annotations

import base64
import hashlib
import os
import struct
from typing import NamedTuple

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONTINUATION = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


class Frame(NamedTuple):
    opcode: int
    payload: bytes
    fin: bool = True


def accept_key(sec_websocket_key: str) -> str:
    digest = hashlib.sha1((sec_websocket_key.strip() + WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def server_handshake_response(sec_websocket_key: str) -> bytes:
    accept = accept_key(sec_websocket_key)
    return (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n"
        "\r\n"
    ).encode("ascii")


def parse_http_headers(raw: bytes) -> tuple[str, dict[str, str]]:
    text = raw.decode("latin-1", errors="replace")
    lines = text.split("\r\n")
    request_line = lines[0] if lines else ""
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line or ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    return request_line, headers


def mask_payload(payload: bytes, mask: bytes) -> bytes:
    return bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))


def encode_frame(opcode: int, payload: bytes, *, mask: bool) -> bytes:
    fin_opcode = 0x80 | (opcode & 0x0F)
    length = len(payload)
    if length < 126:
        header = bytearray((fin_opcode, (0x80 if mask else 0x00) | length))
    elif length < 65536:
        header = bytearray((fin_opcode, (0x80 if mask else 0x00) | 126))
        header.extend(struct.pack("!H", length))
    else:
        header = bytearray((fin_opcode, (0x80 if mask else 0x00) | 127))
        header.extend(struct.pack("!Q", length))
    if mask:
        key = os.urandom(4)
        return bytes(header) + key + mask_payload(payload, key)
    return bytes(header) + payload


def encode_text(message: str, *, mask: bool) -> bytes:
    return encode_frame(OP_TEXT, message.encode("utf-8"), mask=mask)


def try_decode_frame(buffer: bytearray) -> tuple[Frame | None, int]:
    """Return (frame, bytes_consumed). Frame is None if more data is needed."""
    if len(buffer) < 2:
        return None, 0
    b0, b1 = buffer[0], buffer[1]
    fin = bool(b0 & 0x80)
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    length = b1 & 0x7F
    index = 2
    if length == 126:
        if len(buffer) < 4:
            return None, 0
        length = int.from_bytes(buffer[2:4], "big")
        index = 4
    elif length == 127:
        if len(buffer) < 10:
            return None, 0
        length = int.from_bytes(buffer[2:10], "big")
        index = 10
    mask_key = b""
    if masked:
        if len(buffer) < index + 4:
            return None, 0
        mask_key = bytes(buffer[index:index + 4])
        index += 4
    if len(buffer) < index + length:
        return None, 0
    payload = bytes(buffer[index:index + length])
    if masked:
        payload = mask_payload(payload, mask_key)
    return Frame(opcode, payload, fin), index + length


def connect_upstream(host: str, port: int, timeout: float = 2.0):
    """Open a client WebSocket to Restim /tcode. Returns a connected socket."""
    import socket

    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET /tcode HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    sock.sendall(request.encode("ascii"))
    response = b""
    while b"\r\n\r\n" not in response and len(response) < 16384:
        chunk = sock.recv(4096)
        if not chunk:
            break
        response += chunk
    expected = accept_key(key).encode("ascii").lower()
    if not response.startswith(b"HTTP/1.1 101") or expected not in response.lower():
        sock.close()
        raise OSError(f"Upstream Restim rejected WebSocket handshake on {host}:{port}")
    sock.settimeout(None)
    return sock
