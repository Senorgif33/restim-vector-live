from __future__ import annotations

import base64
import hashlib
import os
import socket
import select
import struct
import threading
import time
from collections import deque
from typing import Callable

from .tcode import (
    EvtTrigger, format_command, is_evt_line, is_path_line, parse_evt_line,
    parse_path_line, parse_message,
)
from .websocket_input import WebSocketInput


class MFPListener:
    """TCP/WebSocket (shared port) and UDP input with one stream client at a time."""
    def __init__(self, on_l0: Callable[[float, int, float], None], status: Callable[[str], None],
                 on_command: Callable[[object, float], None] | None = None,
                 on_evt: Callable[[EvtTrigger, float], None] | None = None,
                 on_path: Callable[[str, float], None] | None = None) -> None:
        self.on_l0, self.status = on_l0, status
        self.on_command = on_command
        self.on_evt = on_evt
        self.on_path = on_path
        self._run = threading.Event()
        self._threads: list[threading.Thread] = []
        self._sockets: list[socket.socket] = []
        self._socket_lock = threading.Lock()
        self._last_received: float | None = None
        self._last_transport = ""
        self._transport_live = {"tcp": False, "udp": False}
        self._raw_lock = threading.Lock()
        self._raw_packets = deque(maxlen=80)

    def start(self, host: str, port: int) -> None:
        self.stop()
        self._last_received = None
        self._last_transport = ""
        self._run.set()
        for transport in ("tcp", "udp"):
            thread = threading.Thread(target=self._supervise, args=(transport, host, port),
                                      name=f"mfp-{transport}", daemon=True)
            self._threads.append(thread)
            thread.start()
        self.status(f"Listening on {host}:{port} (TCP + UDP + WebSocket)")

    def stop(self) -> None:
        self._run.clear()
        with self._socket_lock:
            sockets, self._sockets = self._sockets, []
        for sock in sockets:
            try: sock.close()
            except OSError: pass
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(timeout=1.0)
        self._threads.clear()
        self.status("Disconnected")

    def connection_label(self) -> str:
        if not self._run.is_set():
            return "Disconnected"
        if self._last_received is None:
            return "Listening"
        age = time.monotonic() - self._last_received
        return f"Receiving ({self._last_transport})" if age < 2.0 else f"Listening; no L0 for {age:.1f} s"

    def health(self) -> dict[str, object]:
        """Return read-only listener health without treating a quiet script as failure."""
        return {
            "running": self._run.is_set(),
            "tcp": self._transport_live["tcp"],
            "udp": self._transport_live["udp"],
            "websocket": self._transport_live["tcp"],
            "last_transport": self._last_transport,
            "last_l0_age": (None if self._last_received is None
                            else time.monotonic() - self._last_received),
        }

    def _track(self, sock: socket.socket, add: bool) -> None:
        with self._socket_lock:
            if add: self._sockets.append(sock)
            elif sock in self._sockets: self._sockets.remove(sock)

    def _supervise(self, transport: str, host: str, port: int) -> None:
        while self._run.is_set():
            try:
                (self._tcp_session if transport == "tcp" else self._udp_session)(host, port)
            except OSError as exc:
                if self._run.is_set():
                    self.status(f"MFP {transport.upper()} recovering: {exc}")
            finally:
                self._transport_live[transport] = False
            if self._run.is_set():
                time.sleep(0.5)

    def recent_packets(self, limit: int = 20) -> list[dict[str, object]]:
        """Return recent raw MFP packets and the axes parsed from each packet."""
        with self._raw_lock:
            return list(self._raw_packets)[-max(1, int(limit)):]

    def _handle(self, text: str, transport: str = "?") -> None:
        received_at = time.monotonic()
        stripped = text.strip()
        if is_evt_line(stripped):
            with self._raw_lock:
                self._raw_packets.append({
                    "time": received_at,
                    "transport": transport.upper(),
                    "raw": stripped,
                    "axes": ["EVT"],
                })
            try:
                trigger = parse_evt_line(stripped)
            except ValueError:
                return
            if self.on_evt is not None:
                self.on_evt(trigger, received_at)
            return
        if is_path_line(stripped):
            with self._raw_lock:
                self._raw_packets.append({
                    "time": received_at,
                    "transport": transport.upper(),
                    "raw": stripped,
                    "axes": ["PATH"],
                })
            try:
                media_path = parse_path_line(stripped)
            except ValueError:
                return
            if self.on_path is not None:
                self.on_path(media_path, received_at)
            return
        commands = parse_message(text)
        with self._raw_lock:
            self._raw_packets.append({
                "time": received_at,
                "transport": transport.upper(),
                "raw": stripped,
                "axes": [command.axis for command in commands],
            })
        for command in commands:
            if self.on_command is not None:
                self.on_command(command, received_at)
            if command.axis == "L0":
                self._last_received = received_at
                self._last_transport = transport.upper()
                self.on_l0(command.value, command.interval_ms, received_at)

    def _tcp_session(self, host: str, port: int) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM); self._track(server, True)
        try:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((host, port)); server.listen(4); server.settimeout(0.5)
            self._transport_live["tcp"] = True
            while self._run.is_set():
                try: client, _ = server.accept()
                except socket.timeout: continue
                self._track(client, True)
                client.settimeout(0.5)
                try:
                    self._stream_client(client)
                except (OSError, EOFError) as exc:
                    if self._run.is_set() and isinstance(exc, OSError):
                        self.status("MFP stream disconnected; listening for reconnection")
                finally:
                    self._track(client, False)
                    client.close()
        finally:
            self._track(server, False); server.close()

    def _stream_client(self, client: socket.socket) -> None:
        # 'G' cannot start a T-code axis. Inspect only the first received byte,
        # so existing TCP input does not gain a detection delay.
        initial = b""
        while self._run.is_set() and not initial:
            try:
                initial = client.recv(4096)
            except socket.timeout:
                continue
            if not initial:
                return
        if not self._run.is_set():
            return
        if initial.startswith(b"G"):
            websocket = WebSocketInput(client, self._run.is_set, initial)
            if websocket.handshake():
                websocket.receive(lambda text: self._handle(text, "websocket"))
            return
        buffer = ""
        data = initial
        while self._run.is_set():
            buffer += data.decode("ascii", errors="ignore")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                self._handle(line, "tcp")
            if " " in buffer:
                tokens = buffer.split(" ")
                buffer = tokens.pop()
                self._handle(" ".join(tokens), "tcp")
            if len(buffer) > WebSocketInput.MAX_MESSAGE:
                return
            while self._run.is_set():
                try:
                    data = client.recv(4096)
                    break
                except socket.timeout:
                    continue
            if not data:
                return

    def _udp_session(self, host: str, port: int) -> None:
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); self._track(udp, True)
        try:
            udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            udp.bind((host, port)); udp.settimeout(0.5)
            self._transport_live["udp"] = True
            while self._run.is_set():
                try: data, _ = udp.recvfrom(65535)
                except socket.timeout: continue
                self._handle(data.decode("utf-8", errors="replace"), "udp")
        finally:
            self._track(udp, False); udp.close()


class LatestFrameDispatcher:
    """One non-blocking, latest-frame-only output lane.

    The deterministic engine submits immutable send callables and never performs
    socket I/O. A blocked destination can delay only its own worker. Superseded or
    stale frames are discarded rather than replayed after a dropout.
    """
    def __init__(self, name: str, status: Callable[[str], None],
                 max_age_seconds: float = 0.25) -> None:
        self.name = name
        self.status = status
        self.max_age_seconds = max(0.02, float(max_age_seconds))
        self._condition = threading.Condition()
        self._pending: tuple[float, int, Callable[[], bool]] | None = None
        self._closed = False
        self._sequence = 0
        self._submitted = 0
        self._completed = 0
        self._dropped = 0
        self._stale = 0
        self._failures = 0
        self._last_submit: float | None = None
        self._last_complete: float | None = None
        self._last_duration: float | None = None
        self._worker = threading.Thread(
            target=self._run, name=f"restim-send-{name.lower()}", daemon=True)
        self._worker.start()

    def submit(self, send: Callable[[], bool], *, max_age_seconds: float | None = None) -> int:
        now = time.monotonic()
        with self._condition:
            self._sequence += 1
            sequence = self._sequence
            if self._pending is not None:
                self._dropped += 1
            age = self.max_age_seconds if max_age_seconds is None else max(0.02, float(max_age_seconds))
            self._pending = (now + age, sequence, send)
            self._submitted += 1
            self._last_submit = now
            self._condition.notify()
            return sequence

    def health(self) -> dict[str, object]:
        with self._condition:
            now = time.monotonic()
            return {
                "alive": self._worker.is_alive(),
                "pending": self._pending is not None,
                "submitted": self._submitted,
                "completed": self._completed,
                "dropped": self._dropped,
                "stale": self._stale,
                "failures": self._failures,
                "submit_age": None if self._last_submit is None else now - self._last_submit,
                "complete_age": None if self._last_complete is None else now - self._last_complete,
                "send_duration": self._last_duration,
            }

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._pending = None
            self._condition.notify_all()
        if self._worker is not threading.current_thread():
            self._worker.join(timeout=2.5)

    def _run(self) -> None:
        while True:
            with self._condition:
                while self._pending is None and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                deadline, sequence, send = self._pending
                self._pending = None
            now = time.monotonic()
            if now > deadline:
                with self._condition:
                    self._stale += 1
                self.status(f"{self.name} discarded stale output frame {sequence}")
                continue
            started = now
            try:
                successful = send()
                duration = time.monotonic() - started
                with self._condition:
                    self._last_duration = duration
                    if successful is False:
                        self._failures += 1
                    else:
                        self._completed += 1
                        self._last_complete = time.monotonic()
                if duration > 0.10:
                    self.status(f"{self.name} slow send {duration:.3f}s")
            except Exception as exc:
                with self._condition:
                    self._failures += 1
                    self._last_duration = time.monotonic() - started
                self.status(f"{self.name} sender recovered from {type(exc).__name__}: {exc}")


class _ReconnectClient:
    MAX_RECONNECT_ATTEMPTS = 5
    RECONNECT_RETRY_SECONDS = 2.0
    CONNECT_TIMEOUT = 2.0

    def __init__(self, status: Callable[[str], None]) -> None:
        self.status, self._socket = status, None
        self._lock = threading.Lock()
        self._target: tuple[str, int] | None = None
        self._retry_at = 0.0
        self._manual_disconnect = True
        self._last_send: float | None = None
        self._previous_send: float | None = None
        self._send_count = 0
        self._send_failures = 0
        self._reconnect_attempts = 0
        self._reconnect_successes = 0
        self._consecutive_failures = 0
        self._closed = threading.Event()
        self._monitor = threading.Thread(target=self._monitor_connection,
                                         name="restim-reconnect", daemon=True)
        self._monitor.start()

    @property
    def connected(self) -> bool: return self._socket is not None

    def health(self) -> dict[str, object]:
        with self._lock:
            now = time.monotonic()
            return {
                "connected": self._socket is not None,
                "socket_state": "connected" if self._socket is not None else "disconnected",
                "tx_age": None if self._last_send is None else now - self._last_send,
                "send_cadence": (None if self._last_send is None or self._previous_send is None
                                  else self._last_send - self._previous_send),
                "send_count": self._send_count,
                "send_failures": self._send_failures,
                "reconnect_attempts": self._reconnect_attempts,
                "reconnect_successes": self._reconnect_successes,
            }

    def _sent(self) -> None:
        now = time.monotonic()
        self._previous_send, self._last_send = self._last_send, now
        self._send_count += 1

    def connect(self, host: str, port: int) -> None:
        self.disconnect(False)
        self._target = (host, port)
        self._manual_disconnect = False
        self._consecutive_failures = 0
        try:
            with self._lock:
                message = self._connect_locked()
                self._consecutive_failures = 0
        except OSError:
            self._retry_at = time.monotonic() + min(1.0, self.RECONNECT_RETRY_SECONDS)
            raise
        if message:
            self.status(message)

    def disconnect(self, manual: bool = True) -> None:
        with self._lock:
            self._drop_socket_locked()
        self._manual_disconnect = manual
        if manual:
            self._target = None
        self.status("Disconnected")

    def _drop_socket_locked(self) -> None:
        """Close the live socket. Caller must hold ``_lock``."""
        if self._socket:
            try:
                self._socket.close()
            except OSError:
                pass
        self._socket = None

    def _fail_locked(self, exc: Exception) -> str:
        """Drop the socket after an I/O failure. Caller must hold ``_lock``.

        Returns a status string; never call ``status`` while the lock is held —
        that deadlocks the UI when the status callback touches Tk.
        """
        self._drop_socket_locked()
        self._retry_at = time.monotonic() + min(1.0, self.RECONNECT_RETRY_SECONDS)
        self._send_failures += 1
        return f"Disconnected; reconnecting: {exc}"

    def _give_up_locked(self) -> str:
        """Stop auto-reconnect until the user hits Connect. Caller holds ``_lock``."""
        self._drop_socket_locked()
        self._manual_disconnect = True
        self._target = None
        return f"Disconnected (gave up after {self.MAX_RECONNECT_ATTEMPTS} attempts)"

    def _note_reconnect_failure_locked(self) -> str | None:
        """Count a failed attempt. Returns give-up status text, or None to keep retrying."""
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.MAX_RECONNECT_ATTEMPTS:
            return self._give_up_locked()
        self._retry_at = time.monotonic() + self.RECONNECT_RETRY_SECONDS
        return None

    def _connect_locked(self) -> str:
        """Establish a new socket. Caller must hold ``_lock``. Returns status text."""
        raise NotImplementedError

    def _try_reconnect(self) -> None:
        """Reconnect without holding the lock across status callbacks."""
        with self._lock:
            if self._manual_disconnect or not self._target or self._socket is not None:
                return
            if time.monotonic() < self._retry_at:
                return
            self._reconnect_attempts += 1
            attempt = self._reconnect_attempts
        self.status(f"Reconnect attempt {attempt}")
        try:
            with self._lock:
                if self._manual_disconnect or not self._target or self._socket is not None:
                    return
                message = self._connect_locked()
                self._reconnect_successes += 1
                self._consecutive_failures = 0
                success = self._reconnect_successes
            self.status(f"Reconnect succeeded ({success})")
            if message:
                self.status(message)
        except OSError as exc:
            with self._lock:
                gave_up = self._note_reconnect_failure_locked()
            self.status(gave_up or f"Reconnecting: {exc}")

    def _ensure_connected_locked(self) -> tuple[bool, list[str]]:
        """Ensure a live socket. Caller must hold ``_lock``.

        Returns ``(ok, status_messages)``. Status is emitted by the caller
        *after* releasing the lock.
        """
        if self._socket:
            return True, []
        if self._manual_disconnect or not self._target or time.monotonic() < self._retry_at:
            return False, []
        messages: list[str] = []
        self._reconnect_attempts += 1
        messages.append(f"Reconnect attempt {self._reconnect_attempts}")
        try:
            messages.append(self._connect_locked())
            self._reconnect_successes += 1
            self._consecutive_failures = 0
            messages.append(f"Reconnect succeeded ({self._reconnect_successes})")
            return True, messages
        except OSError as exc:
            gave_up = self._note_reconnect_failure_locked()
            messages.append(gave_up or f"Reconnecting: {exc}")
            return False, messages

    def _monitor_connection(self) -> None:
        """Reconnect independently of output flow and notice silent peer closure."""
        while not self._closed.wait(0.5):
            status_message: str | None = None
            need_reconnect = False
            with self._lock:
                if self._manual_disconnect or not self._target:
                    continue
                if self._socket is None:
                    need_reconnect = True
                else:
                    try:
                        readable, _, _ = select.select([self._socket], [], [], 0)
                        if readable and self._socket.recv(1, socket.MSG_PEEK) == b"":
                            raise ConnectionResetError("ReStim closed the connection")
                    except (OSError, ValueError) as exc:
                        status_message = self._fail_locked(exc)
            if status_message:
                self.status(status_message)
            if need_reconnect:
                self._try_reconnect()

    def close(self) -> None:
        self._closed.set()
        self.disconnect()
        if self._monitor is not threading.current_thread():
            self._monitor.join(timeout=1.0)


# Position-like axes get sample-period I suffixes so Restim can interpolate.
_RESTIM_POSITION_AXES = frozenset({"L0", "L1", "E1", "E2", "E3", "E4"})


def _format_restim_axis(axis: str, value: float, interval_ms: int = 0) -> str:
    """Format one T-code axis; attach I only on position axes when interval_ms > 0."""
    if interval_ms > 0 and axis.upper() in _RESTIM_POSITION_AXES:
        return format_command(axis, value, interval_ms)
    return format_command(axis, value)


class ReStimClient(_ReconnectClient):
    def _connect_locked(self) -> str:
        assert self._target
        host, port = self._target
        sock = socket.create_connection((host, port), timeout=self.CONNECT_TIMEOUT)
        sock.settimeout(self.CONNECT_TIMEOUT)
        self._socket = sock
        return f"Connected to {host}:{port}"

    def send(self, alpha: float, beta: float, volume: float, frequency=None,
             pulse_frequency=None, pulse_rise_time=None, pulse_width=None,
             interval_ms: int = 0) -> None:
        commands = [_format_restim_axis("L0", alpha, interval_ms),
                    _format_restim_axis("L1", beta, interval_ms),
                    format_command("V0", volume)]
        for axis, value in (("C0", frequency), ("P0", pulse_frequency),
                            ("P3", pulse_rise_time), ("P1", pulse_width)):
            if value is not None: commands.append(format_command(axis, value))
        messages: list[str] = []
        with self._lock:
            ok, messages = self._ensure_connected_locked()
            if not ok:
                pass
            else:
                try:
                    self._socket.sendall((" ".join(commands) + "\n").encode("ascii"))
                except OSError as exc:
                    messages.append(self._fail_locked(exc))
        for message in messages:
            if message:
                self.status(message)


class ReStimWebSocketClient(_ReconnectClient):
    """Dependency-free RFC 6455 client for ReStim's /tcode endpoint."""
    def _connect_locked(self) -> str:
        assert self._target
        host, port = self._target
        sock = socket.create_connection((host, port), timeout=self.CONNECT_TIMEOUT)
        sock.settimeout(self.CONNECT_TIMEOUT)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (f"GET /tcode HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response and len(response) < 16384:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
        expected = base64.b64encode(
            hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        if not response.startswith(b"HTTP/1.1 101") or expected.lower() not in response.lower():
            sock.close()
            raise OSError("ReStim rejected the WebSocket handshake")
        self._socket = sock
        self._needs_neutral = getattr(self, "_has_connected", False)
        self._has_connected = True
        return f"Connected to ws://{host}:{port}/tcode"

    @staticmethod
    def _frame(message: str) -> bytes:
        payload, mask = message.encode("utf-8"), os.urandom(4); length = len(payload)
        header = bytearray((0x81, 0x80 | min(length, 126)))
        if length >= 126: header = bytearray((0x81, 0xFE)) + struct.pack("!H", length)
        return bytes(header) + mask + bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))

    def _send_message(self, message: str, neutral: str) -> bool:
        status_messages: list[str] = []
        successful = False
        with self._lock:
            ok, status_messages = self._ensure_connected_locked()
            if ok:
                try:
                    if getattr(self, "_needs_neutral", False):
                        self._socket.sendall(self._frame(neutral))
                        self._needs_neutral = False
                        status_messages.append("Recovered safely; output resumed")
                    self._socket.sendall(self._frame(message))
                    self._sent()
                    successful = True
                except OSError as exc:
                    status_messages.append(self._fail_locked(exc))
                    successful = False
        for text in status_messages:
            if text:
                self.status(text)
        return successful

    def send_prostate(self, alpha, beta, volume, frequency, pulse_frequency,
                      pulse_width, pulse_rise_time, interval_ms: int = 0,
                      s1: float | None = None) -> bool:
        commands = [
            _format_restim_axis("L0", alpha, interval_ms),
            _format_restim_axis("L1", beta, interval_ms),
            format_command("V0", volume), format_command("F0", frequency),
            format_command("P0", pulse_frequency), format_command("P1", pulse_width),
            format_command("P3", pulse_rise_time),
        ]
        # Same sensor value as primary. Omit when primary has no S1 this frame.
        if s1 is not None:
            commands.append(_format_restim_axis("S1", min(1.0, max(0.0, s1))))
        message = " ".join(commands)
        neutral = " ".join((format_command("L0", .5), format_command("L1", .5),
                            format_command("V0", 0.0)))
        return self._send_message(message, neutral)

    def send_primary(self, alpha: float, beta: float,
                     electrodes: tuple[float, float, float, float], volume: float,
                     frequency: float, pulse_frequency: float,
                     pulse_rise_time: float, pulse_width: float,
                     overrides: dict[str, float] | None = None,
                     interval_ms: int = 0) -> bool:
        values = {
            "L0": alpha, "L1": beta,
            "E1": electrodes[0], "E2": electrodes[1],
            "E3": electrodes[2], "E4": electrodes[3],
            "V0": volume, "C0": frequency, "P0": pulse_frequency,
            "P3": pulse_rise_time, "P1": pulse_width,
        }
        if overrides:
            values.update({axis.upper(): min(1.0, max(0.0, value))
                           for axis, value in overrides.items()})
        preferred_order = ("L0", "L1", "E1", "E2", "E3", "E4",
                           "V0", "C0", "P0", "P3", "P1")
        ordered_axes = list(preferred_order)
        ordered_axes.extend(sorted(axis for axis in values if axis not in preferred_order))
        commands = [_format_restim_axis(axis, values[axis], interval_ms)
                    for axis in ordered_axes]
        # Neutral/recovery settles instantly — never attach I suffixes.
        neutral = " ".join([format_command("L0", .5), format_command("L1", .5)] +
                           [format_command(axis, .5) for axis in ("E1", "E2", "E3", "E4")] +
                           [format_command("V0", 0.0)])
        return self._send_message(" ".join(commands), neutral)

    def send_four_phase(self, values: tuple[float, float, float, float],
                        volume: float | None = None, frequency: float | None = None,
                        pulse_frequency: float | None = None,
                        pulse_rise_time: float | None = None,
                        pulse_width: float | None = None,
                        interval_ms: int = 0) -> bool:
        commands = [_format_restim_axis(axis, value, interval_ms)
                    for axis, value in zip(("E1", "E2", "E3", "E4"), values)]
        if volume is not None:
            commands.append(format_command("V0", volume))
        for axis, value in (("C0", frequency), ("P0", pulse_frequency),
                            ("P3", pulse_rise_time), ("P1", pulse_width)):
            if value is not None:
                commands.append(format_command(axis, value))
        message = " ".join(commands)
        neutral = " ".join([format_command(axis, .5)
                            for axis in ("E1", "E2", "E3", "E4")] +
                           [format_command("V0", 0.0)])
        return self._send_message(message, neutral)
