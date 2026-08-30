"""Embedded LAN HTTP + WebSocket control API for Vector 1A (stdlib only)."""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Protocol
from urllib.parse import urlparse

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_TEXT = 0x1
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

# Writable settings exposed remotely (subset of SETTINGS_FIELDS).
DESKTOP_ONLY_FIELDS = frozenset({
    "mfp_launch_target", "restim_launch_target", "prostate_launch_target",
    "events_file_path", "events_definitions_path",
    "four_phase_host", "four_phase_port",
})

CONTROL_META_FIELDS = ("control_api_enabled", "control_api_host", "control_api_port")

PANEL_FIELDS: dict[str, tuple[str, ...]] = {
    "mfp": ("mfp_host", "mfp_port"),
    "restim": ("restim_host", "restim_port", "prostate_host", "prostate_port"),
    "motion": (
        "mode", "rate", "lookahead", "volume", "minimum_radius", "speed_threshold",
        "direction_probability", "jitter_enabled", "jitter_amplitude", "jitter_cycle_seconds",
        "speed_linked_variation", "variation_full_speed_percent", "variation_fade_seconds",
        "motion_rising_volume_multiplier", "motion_falling_volume_multiplier",
    ),
    "volume_response": (
        "dynamic_volume", "volume_rest_level", "volume_ratio", "volume_ramp_up",
        "four_phase_volume_ceiling",
    ),
    "media_volume_ramp": (
        "media_volume_ramp_enabled", "media_volume_ramp_floor", "media_volume_ramp_floor2",
        "media_volume_ramp_floor3", "media_volume_ramp_ceiling", "media_volume_ramp_ceiling2",
        "media_volume_ramp_ceiling3", "media_volume_ramp_curve",
        "media_volume_ramp_waypoints_enabled",
        "timeline_position_axis", "timeline_duration_axis", "timeline_scale_seconds",
    ),
    "events": ("events_enabled",),
    "frequency": ("frequency_ramp_level", "frequency_ratio", "send_frequency"),
    "pulse_frequency": (
        "pulse_frequency_ratio", "pulse_frequency_min", "pulse_frequency_max",
        "send_pulse_frequency",
    ),
    "pulse_rise": ("pulse_rise_ratio", "pulse_rise_min", "pulse_rise_max", "send_pulse_rise"),
    "pulse_width": (
        "pulse_width_ratio", "pulse_width_min", "pulse_width_max", "send_pulse_width",
    ),
    "prostate": (
        "prostate_narrow_ratio", "prostate_arc_depth", "prostate_threshold",
        "prostate_volume_multiplier", "prostate_rest_level", "prostate_phase_degrees",
        "prostate_phase_step",
    ),
    "four_phase": (
        "four_phase_return_depth", "four_phase_invert", "four_phase_volume_ceiling",
        "four_phase_volume_modulation", "four_phase_volume_headroom", "four_phase_volume_cycle",
        "four_phase_crossover_width", "four_phase_crossover_curve", "four_phase_crossover_sharpness",
        "four_phase_adaptive_crossover", "four_phase_slow_crossover_width",
        "four_phase_fast_crossover_width", "four_phase_directional_trajectory",
        "four_phase_reverse_width_scale", "four_phase_reverse_curve", "four_phase_reverse_sharpness",
        "four_phase_spatial_curve", "four_phase_spatial_blend", "four_phase_reversal_emphasis",
        "four_phase_reversal_window", "four_phase_reversal_strength",
        "four_phase_stroke_phase_texture", "four_phase_acceleration_width_scale",
        "four_phase_deceleration_width_scale", "four_phase_group_delay",
        "four_phase_group_delay_ms", "four_phase_group_delay_transition", "electrode_order",
        "four_phase_moving_sequence", "four_phase_moving_sequence_depth",
        "four_phase_moving_sequence_width", "four_phase_spatial_model", "four_phase_tip_retention",
        "four_phase_spread_softness", "four_phase_full_depth_capture",
        "variety_electrode_morph", "variety_electrode_morph_cycle",
        "variety_electrode_morph_transition_seconds",
        "preset_a_name", "preset_b_name", "preset_transition_seconds",
    ),
    "xbox": (
        "controller_enabled", "controller_fine_step", "direct_controller_enabled",
        "prostate_phase_step",
    ),
    "variety": (
        "variety_enabled", "variety_frequency", "variety_pulse_frequency",
        "variety_pulse_rise", "variety_pulse_width", "variety_phase",
        "variety_frequency_cycle", "variety_pulse_frequency_cycle",
        "variety_pulse_rise_cycle", "variety_pulse_width_cycle", "variety_phase_cycle",
        "variety_electrode_morph", "variety_electrode_morph_cycle",
        "variety_electrode_morph_transition_seconds",
    ),
    "control_api": CONTROL_META_FIELDS,
}

ACTIONS = (
    "neutral", "stop", "resume",
    "start_listener", "stop_listener",
    "connect_restim", "disconnect_restim",
    "connect_prostate", "disconnect_prostate",
    "apply_preset_a", "apply_preset_b", "apply_preset_baseline",
    "capture_preset_a", "capture_preset_b", "toggle_preset_ab",
)

STATUS_KEYS = (
    "mfp_status", "restim_status", "prostate_status", "session_ready_status",
    "controller_status", "variety_status", "events_status", "media_ramp_status",
    "timeline_status", "startup_status", "control_api_status", "preset_status",
)


def writable_fields(settings_fields: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    allowed = [name for name in settings_fields if name not in DESKTOP_ONLY_FIELDS]
    for name in CONTROL_META_FIELDS:
        if name not in allowed:
            allowed.append(name)
    return tuple(allowed)


def accept_key(sec_websocket_key: str) -> str:
    digest = hashlib.sha1((sec_websocket_key.strip() + WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def encode_frame(opcode: int, payload: bytes) -> bytes:
    fin_opcode = 0x80 | (opcode & 0x0F)
    length = len(payload)
    if length < 126:
        header = bytes((fin_opcode, length))
    elif length < 65536:
        header = bytes((fin_opcode, 126)) + struct.pack("!H", length)
    else:
        header = bytes((fin_opcode, 127)) + struct.pack("!Q", length)
    return header + payload


def encode_text(message: str) -> bytes:
    return encode_frame(OP_TEXT, message.encode("utf-8"))


def try_decode_frame(buffer: bytearray) -> tuple[tuple[int, bytes] | None, int]:
    if len(buffer) < 2:
        return None, 0
    b0, b1 = buffer[0], buffer[1]
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
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return (opcode, payload), index + length


class ControlBackend(Protocol):
    def control_schema(self) -> dict[str, Any]: ...
    def control_state(self) -> dict[str, Any]: ...
    def control_patch(self, patch: dict[str, Any]) -> dict[str, Any]: ...
    def control_action(self, name: str) -> dict[str, Any]: ...


class ControlApiServer:
    """Threading HTTP server with optional WebSocket stream clients."""

    def __init__(self, backend: ControlBackend, host: str, port: int,
                 stream_hz: float = 10.0) -> None:
        self.backend = backend
        self.host = host
        self.port = port
        self.stream_interval = 1.0 / max(1.0, stream_hz)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._stream_thread: threading.Thread | None = None
        self._run = threading.Event()
        self._clients: list[_StreamClient] = []
        self._clients_lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._httpd is not None and self._run.is_set()

    def start(self) -> None:
        self.stop()
        self._run.set()
        handler = _make_handler(self)
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="vector-control-api", daemon=True)
        self._thread.start()
        self._stream_thread = threading.Thread(
            target=self._broadcast_loop, name="vector-control-stream", daemon=True)
        self._stream_thread.start()

    def stop(self) -> None:
        self._run.clear()
        with self._clients_lock:
            clients, self._clients = self._clients, []
        for client in clients:
            client.close()
        httpd = self._httpd
        self._httpd = None
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._stream_thread is not None:
            self._stream_thread.join(timeout=2.0)
            self._stream_thread = None

    def add_stream_client(self, client: "_StreamClient") -> None:
        with self._clients_lock:
            self._clients.append(client)

    def remove_stream_client(self, client: "_StreamClient") -> None:
        with self._clients_lock:
            if client in self._clients:
                self._clients.remove(client)

    def _broadcast_loop(self) -> None:
        while self._run.is_set():
            started = time.monotonic()
            try:
                payload = json.dumps(
                    {"type": "snapshot", "state": self.backend.control_state()},
                    separators=(",", ":"))
                frame = encode_text(payload)
                with self._clients_lock:
                    clients = list(self._clients)
                dead: list[_StreamClient] = []
                for client in clients:
                    if not client.send_raw(frame):
                        dead.append(client)
                for client in dead:
                    client.close()
                    self.remove_stream_client(client)
            except Exception:
                pass
            elapsed = time.monotonic() - started
            time.sleep(max(0.01, self.stream_interval - elapsed))


class _StreamClient:
    def __init__(self, sock) -> None:
        self.sock = sock
        self._lock = threading.Lock()
        self._closed = False

    def send_raw(self, data: bytes) -> bool:
        if self._closed:
            return False
        try:
            with self._lock:
                self.sock.sendall(data)
            return True
        except OSError:
            self._closed = True
            return False

    def close(self) -> None:
        self._closed = True
        try:
            self.sock.close()
        except OSError:
            pass


def _make_handler(server: ControlApiServer):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

        def _cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def _json(self, status: int, body: dict[str, Any] | list[Any]) -> None:
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self._cors()
            self.end_headers()
            self.wfile.write(payload)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length > 0 else b"{}"
            if not raw:
                return {}
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("JSON body must be an object")
            return data

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path == "/v1/stream":
                if self.headers.get("Upgrade", "").lower() == "websocket":
                    self._handle_websocket()
                    return
                self._json(426, {"error": "WebSocket upgrade required"})
                return
            try:
                if path == "/v1/schema":
                    self._json(200, server.backend.control_schema())
                elif path == "/v1/state":
                    self._json(200, server.backend.control_state())
                elif path in ("/", "/v1"):
                    self._json(200, {
                        "service": "vector1a-control",
                        "endpoints": [
                            "GET /v1/schema", "GET /v1/state", "POST /v1/state",
                            "WS /v1/stream", "POST /v1/actions/{name}",
                        ],
                    })
                else:
                    self._json(404, {"error": "not found"})
            except Exception as exc:
                self._json(500, {"error": str(exc)})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path.rstrip("/") or "/"
            try:
                if path == "/v1/state":
                    patch = self._read_json()
                    result = server.backend.control_patch(patch)
                    status = 200 if result.get("ok", True) else 400
                    self._json(status, result)
                    return
                if path.startswith("/v1/actions/"):
                    name = path[len("/v1/actions/"):]
                    result = server.backend.control_action(name)
                    status = 200 if result.get("ok", True) else 400
                    self._json(status, result)
                    return
                self._json(404, {"error": "not found"})
            except json.JSONDecodeError as exc:
                self._json(400, {"error": f"invalid JSON: {exc}"})
            except Exception as exc:
                self._json(500, {"error": str(exc)})

        def _handle_websocket(self) -> None:
            key = self.headers.get("Sec-WebSocket-Key")
            if not key:
                self._json(400, {"error": "missing Sec-WebSocket-Key"})
                return
            accept = accept_key(key)
            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()
            sock = self.connection
            client = _StreamClient(sock)
            server.add_stream_client(client)
            try:
                snapshot = json.dumps(
                    {"type": "snapshot", "state": server.backend.control_state()},
                    separators=(",", ":"))
                client.send_raw(encode_text(snapshot))
                buffer = bytearray()
                sock.settimeout(1.0)
                while server._run.is_set() and not client._closed:
                    try:
                        chunk = sock.recv(4096)
                    except TimeoutError:
                        continue
                    except OSError:
                        break
                    if not chunk:
                        break
                    buffer.extend(chunk)
                    while True:
                        frame, consumed = try_decode_frame(buffer)
                        if frame is None:
                            break
                        del buffer[:consumed]
                        opcode, payload = frame
                        if opcode == OP_CLOSE:
                            client.send_raw(encode_frame(OP_CLOSE, payload[:2] if payload else b""))
                            return
                        if opcode == OP_PING:
                            client.send_raw(encode_frame(OP_PONG, payload))
            finally:
                server.remove_stream_client(client)
                client.close()

    return Handler


def run_on_ui(root, fn: Callable[[], Any], timeout: float = 5.0) -> Any:
    """Marshal ``fn`` onto the tkinter UI thread and wait for the result."""
    box: dict[str, Any] = {}
    done = threading.Event()

    def wrapper() -> None:
        try:
            box["value"] = fn()
        except Exception as exc:  # noqa: BLE001 — surface to HTTP caller
            box["error"] = exc
        done.set()

    try:
        root.after(0, wrapper)
    except Exception as exc:
        raise RuntimeError(f"UI thread unavailable: {exc}") from exc
    if not done.wait(timeout):
        raise TimeoutError("timed out waiting for UI thread")
    if "error" in box:
        raise box["error"]
    return box.get("value")
