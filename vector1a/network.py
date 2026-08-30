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

from .tcode import EvtTrigger, format_command, is_evt_line, parse_evt_line, parse_message


class MFPListener:
    """Resilient TCP/UDP T-code listener; each transport restarts after failure."""
    def __init__(self, on_l0: Callable[[float, int, float], None], status: Callable[[str], None],
                 on_command: Callable[[object, float], None] | None = None,
                 on_evt: Callable[[EvtTrigger, float], None] | None = None) -> None:
        self.on_l0, self.status = on_l0, status
        self.on_command = on_command
        self.on_evt = on_evt
        self._run = threading.Event()
        self._threads: list[threading.Thread] = []
        self._sockets: list[socket.socket] = []
        self._socket_lock = threading.Lock()
        self._last_received: float | None = None
        self._transport_live = {"tcp": False, "udp": False}
        self._raw_lock = threading.Lock()
        self._raw_packets = deque(maxlen=80)

    def start(self, host: str, port: int) -> None:
        self.stop()
        self._run.set()
        for transport in ("tcp", "udp"):
            thread = threading.Thread(target=self._supervise, args=(transport, host, port),
                                      name=f"mfp-{transport}", daemon=True)
            self._threads.append(thread)
            thread.start()
        self.status(f"Listening on {host}:{port} (TCP + UDP)")

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
        return "Receiving" if age < 2.0 else f"Listening; no L0 for {age:.1f} s"

    def health(self) -> dict[str, object]:
        """Return read-only listener health without treating a quiet script as failure."""
        return {
            "running": self._run.is_set(),
            "tcp": self._transport_live["tcp"],
            "udp": self._transport_live["udp"],
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
                self._transport_live[transport] = True
                (self._tcp_session if transport == "tcp" else self._udp_session)(host, port)
            except OSError as exc:
                if self._run.is_set():
                    self.status(f"MFP {transport.upper()} recovering: {exc}")
            except Exception as exc:
                # Never let a packet-handler bug kill the listen thread.
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
                self.on_l0(command.value, command.interval_ms, received_at)

    def _tcp_session(self, host: str, port: int) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM); self._track(server, True)
        try:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((host, port)); server.listen(4); server.settimeout(0.5)
            while self._run.is_set():
                try: client, _ = server.accept()
                except socket.timeout: continue
                client.settimeout(0.5); buffer = ""
                with client:
                    while self._run.is_set():
                        try: data = client.recv(4096)
                        except socket.timeout: continue
                        if not data: break
                        buffer += data.decode("ascii", errors="ignore")
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            try:
                                self._handle(line, "tcp")
                            except Exception as exc:
                                self.status(f"MFP TCP packet error: {exc}")
                        if " " in buffer:
                            tokens = buffer.split(" "); buffer = tokens.pop()
                            try:
                                self._handle(" ".join(tokens), "tcp")
                            except Exception as exc:
                                self.status(f"MFP TCP packet error: {exc}")
        finally:
            self._track(server, False); server.close()

    def _udp_session(self, host: str, port: int) -> None:
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); self._track(udp, True)
        try:
            udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            udp.bind((host, port)); udp.settimeout(0.5)
            while self._run.is_set():
                try: data, _ = udp.recvfrom(65535)
                except socket.timeout: continue
                try:
                    self._handle(data.decode("ascii", errors="ignore"), "udp")
                except Exception as exc:
                    self.status(f"MFP UDP packet error: {exc}")
        finally:
            self._track(udp, False); udp.close()


class _ReconnectClient:
    def __init__(self, status: Callable[[str], None],
                 on_send: Callable[[str], None] | None = None) -> None:
        self.status, self._socket = status, None
        self.on_send = on_send
        self._lock = threading.Lock()
        self._target: tuple[str, int] | None = None
        self._retry_at = 0.0
        self._manual_disconnect = True
        self._closed = threading.Event()
        self._monitor = threading.Thread(target=self._monitor_connection,
                                         name="restim-reconnect", daemon=True)
        self._monitor.start()

    def _emit_send(self, message: str) -> None:
        callback = self.on_send
        if callback is None:
            return
        try:
            callback(message)
        except Exception:
            pass

    @property
    def connected(self) -> bool: return self._socket is not None

    def connect(self, host: str, port: int) -> None:
        self.disconnect(False)
        target = (host, port)
        self._target = target
        self._manual_disconnect = False
        try:
            sock = self._open_connection(target)
        except OSError:
            self._retry_at = time.monotonic() + 1.0
            raise
        self._install_socket(sock, target)

    def disconnect(self, manual: bool = True) -> None:
        with self._lock:
            if self._socket:
                try: self._socket.close()
                except OSError: pass
            self._socket = None
        self._manual_disconnect = manual
        if manual: self._target = None
        self.status("Disconnected")

    def _open_connection(self, target: tuple[str, int]):
        raise NotImplementedError

    def _install_socket(self, sock, target: tuple[str, int]) -> None:
        """Attach a live socket, or discard it if the target changed mid-connect."""
        with self._lock:
            if self._manual_disconnect or self._target != target or self._socket is not None:
                try:
                    sock.close()
                except OSError:
                    pass
                return
            self._socket = sock
            self._on_socket_installed(target)

    def _on_socket_installed(self, target: tuple[str, int]) -> None:
        host, port = target
        self.status(f"Connected to {host}:{port}")

    def _attempt_reconnect(self) -> None:
        """Background reconnect; never holds the send lock during blocking I/O."""
        target = self._target
        if self._manual_disconnect or not target or time.monotonic() < self._retry_at:
            return
        with self._lock:
            if self._socket is not None or self._manual_disconnect or self._target != target:
                return
        try:
            sock = self._open_connection(target)
        except OSError as exc:
            self._retry_at = time.monotonic() + 2.0
            self.status(f"Reconnecting: {exc}")
            return
        self._install_socket(sock, target)

    def _monitor_connection(self) -> None:
        """Reconnect independently of output flow and notice silent peer closure."""
        while not self._closed.wait(0.5):
            should_reconnect = False
            with self._lock:
                if self._manual_disconnect or not self._target:
                    pass
                elif self._socket is not None:
                    try:
                        readable, _, _ = select.select([self._socket], [], [], 0)
                        if readable and self._socket.recv(1, socket.MSG_PEEK) == b"":
                            raise ConnectionResetError("ReStim closed the connection")
                    except (OSError, ValueError) as exc:
                        self._failed(exc)
                elif time.monotonic() >= self._retry_at:
                    should_reconnect = True
            if should_reconnect:
                self._attempt_reconnect()

    def close(self) -> None:
        self._closed.set()
        self.disconnect()
        if self._monitor is not threading.current_thread():
            self._monitor.join(timeout=1.0)

    def _failed(self, exc: Exception) -> None:
        if self._socket:
            try: self._socket.close()
            except OSError: pass
        self._socket = None; self._retry_at = time.monotonic() + 1.0
        self.status(f"Disconnected; reconnecting: {exc}")


class ReStimClient(_ReconnectClient):
    def _open_connection(self, target: tuple[str, int]):
        host, port = target
        sock = socket.create_connection((host, port), timeout=2.0)
        sock.settimeout(2.0)
        return sock

    def send(self, alpha: float, beta: float, volume: float, frequency=None,
             pulse_frequency=None, pulse_rise_time=None, pulse_width=None) -> None:
        commands = [format_command("L0", alpha), format_command("L1", beta), format_command("V0", volume)]
        for axis, value in (("C0", frequency), ("P0", pulse_frequency),
                            ("P3", pulse_rise_time), ("P1", pulse_width)):
            if value is not None: commands.append(format_command(axis, value))
        with self._lock:
            if self._socket is None:
                return
            try:
                self._socket.sendall((" ".join(commands) + "\n").encode("ascii"))
            except OSError as exc:
                self._failed(exc)


class ReStimWebSocketClient(_ReconnectClient):
    """Dependency-free RFC 6455 client for ReStim's /tcode endpoint."""
    def _open_connection(self, target: tuple[str, int]):
        host, port = target
        sock = socket.create_connection((host, port), timeout=2.0)
        sock.settimeout(2.0)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (f"GET /tcode HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
                   f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        sock.sendall(request.encode("ascii")); response = b""
        while b"\r\n\r\n" not in response and len(response) < 16384:
            chunk = sock.recv(4096)
            if not chunk: break
            response += chunk
        expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        if not response.startswith(b"HTTP/1.1 101") or expected.lower() not in response.lower():
            sock.close(); raise OSError("ReStim rejected the WebSocket handshake")
        return sock

    def _on_socket_installed(self, target: tuple[str, int]) -> None:
        self._needs_neutral = getattr(self, "_has_connected", False)
        self._has_connected = True
        host, port = target
        self.status(f"Connected to ws://{host}:{port}/tcode")

    @staticmethod
    def _frame(message: str) -> bytes:
        payload, mask = message.encode("utf-8"), os.urandom(4); length = len(payload)
        header = bytearray((0x81, 0x80 | min(length, 126)))
        if length >= 126: header = bytearray((0x81, 0xFE)) + struct.pack("!H", length)
        return bytes(header) + mask + bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))

    def _send_message(self, message: str, neutral: str) -> None:
        # Fail-fast: never reconnect from the send path. A dead peer (e.g. prostate
        # ReStim closed) must not stall primary output on a 2s connect timeout.
        with self._lock:
            if self._socket is None:
                return
            try:
                if getattr(self, "_needs_neutral", False):
                    self._socket.sendall(self._frame(neutral))
                    self._needs_neutral = False
                    self.status("Recovered safely; output resumed")
                self._socket.sendall(self._frame(message))
            except OSError as exc:
                self._failed(exc)

    def send_prostate(self, alpha, beta, volume, frequency, pulse_frequency,
                      pulse_width, pulse_rise_time) -> None:
        message = " ".join((format_command("L0", alpha), format_command("L1", beta),
                            format_command("V0", volume), format_command("F0", frequency),
                            format_command("P0", pulse_frequency), format_command("P1", pulse_width),
                            format_command("P3", pulse_rise_time)))
        neutral = " ".join((format_command("L0", .5), format_command("L1", .5),
                            format_command("V0", 0.0)))
        self._send_message(message, neutral)

    def send_primary(self, alpha: float, beta: float,
                     electrodes: tuple[float, float, float, float], volume: float,
                     frequency: float, pulse_frequency: float,
                     pulse_rise_time: float, pulse_width: float,
                     overrides: dict[str, float] | None = None) -> None:
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
        commands = [format_command(axis, values[axis]) for axis in ordered_axes]
        neutral = " ".join([format_command("L0", .5), format_command("L1", .5)] +
                           [format_command(axis, .5) for axis in ("E1", "E2", "E3", "E4")] +
                           [format_command("V0", 0.0)])
        self._send_message(" ".join(commands), neutral)

    def send_four_phase(self, values: tuple[float, float, float, float],
                        volume: float | None = None, frequency: float | None = None,
                        pulse_frequency: float | None = None,
                        pulse_rise_time: float | None = None,
                        pulse_width: float | None = None) -> None:
        commands = [format_command(axis, value)
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
        self._send_message(message, neutral)
