from __future__ import annotations

import socket
import threading
import time
from typing import Callable

from .tcode import format_command, parse_message


class MFPListener:
    def __init__(self, on_l0: Callable[[float, int, float], None], status: Callable[[str], None]) -> None:
        self.on_l0 = on_l0
        self.status = status
        self._run = threading.Event()
        self._threads: list[threading.Thread] = []
        self._sockets: list[socket.socket] = []
        self._last_received = 0.0

    def start(self, host: str, port: int) -> None:
        self.stop()
        self._run.set()
        for target, name in ((self._tcp_loop, "mfp-tcp"), (self._udp_loop, "mfp-udp")):
            thread = threading.Thread(target=target, args=(host, port), name=name, daemon=True)
            self._threads.append(thread)
            thread.start()
        self.status(f"Listening on {host}:{port} (TCP + UDP)")

    def stop(self) -> None:
        self._run.clear()
        for sock in self._sockets:
            try:
                sock.close()
            except OSError:
                pass
        self._sockets.clear()
        self._threads.clear()
        self.status("Disconnected")

    def connection_label(self) -> str:
        age = time.monotonic() - self._last_received
        return "Receiving" if age < 2.0 else ("Listening" if self._run.is_set() else "Disconnected")

    def _handle(self, text: str) -> None:
        received_at = time.monotonic()
        for command in parse_message(text):
            if command.axis == "L0":
                self._last_received = received_at
                self.on_l0(command.value, command.interval_ms, received_at)

    def _tcp_loop(self, host: str, port: int) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sockets.append(server)
        try:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((host, port))
            server.listen(4)
            server.settimeout(0.5)
            while self._run.is_set():
                try:
                    client, _ = server.accept()
                except socket.timeout:
                    continue
                client.settimeout(0.5)
                buffer = ""
                with client:
                    while self._run.is_set():
                        try:
                            data = client.recv(4096)
                        except socket.timeout:
                            continue
                        if not data:
                            break
                        buffer += data.decode("ascii", errors="ignore")
                        # T-code streams normally delimit batches with newline.
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            self._handle(line)
                        # Also accept space-delimited, non-newline MFP batches.
                        if " " in buffer:
                            tokens = buffer.split(" ")
                            buffer = tokens.pop()
                            self._handle(" ".join(tokens))
        except OSError as exc:
            if self._run.is_set():
                self.status(f"MFP listener error: {exc}")
        finally:
            try:
                server.close()
            except OSError:
                pass

    def _udp_loop(self, host: str, port: int) -> None:
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sockets.append(udp)
        try:
            udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            udp.bind((host, port))
            udp.settimeout(0.5)
            while self._run.is_set():
                try:
                    data, _ = udp.recvfrom(65535)
                except socket.timeout:
                    continue
                self._handle(data.decode("ascii", errors="ignore"))
        except OSError as exc:
            if self._run.is_set():
                self.status(f"MFP UDP error: {exc}")
        finally:
            try:
                udp.close()
            except OSError:
                pass


class ReStimClient:
    def __init__(self, status: Callable[[str], None]) -> None:
        self.status = status
        self._socket: socket.socket | None = None
        self._lock = threading.Lock()

    @property
    def connected(self) -> bool:
        return self._socket is not None

    def connect(self, host: str, port: int) -> None:
        self.disconnect()
        sock = socket.create_connection((host, port), timeout=2.0)
        sock.settimeout(2.0)
        self._socket = sock
        self.status(f"Connected to {host}:{port}")

    def disconnect(self) -> None:
        with self._lock:
            if self._socket:
                try:
                    self._socket.close()
                except OSError:
                    pass
            self._socket = None
        self.status("Disconnected")

    def send(self, alpha: float, beta: float, volume: float,
             frequency: float | None = None,
             pulse_frequency: float | None = None,
             pulse_rise_time: float | None = None,
             pulse_width: float | None = None) -> None:
        commands = [
            format_command("L0", alpha), format_command("L1", beta),
            format_command("V0", volume),
        ]
        if frequency is not None:
            commands.append(format_command("C0", frequency))
        if pulse_frequency is not None:
            commands.append(format_command("P0", pulse_frequency))
        if pulse_rise_time is not None:
            commands.append(format_command("P3", pulse_rise_time))
        if pulse_width is not None:
            commands.append(format_command("P1", pulse_width))
        message = " ".join(commands) + "\n"
        with self._lock:
            if not self._socket:
                return
            try:
                self._socket.sendall(message.encode("ascii"))
            except OSError as exc:
                try:
                    self._socket.close()
                except OSError:
                    pass
                self._socket = None
                self.status(f"Disconnected: {exc}")


class ReStimWebSocketClient:
    """T-code WebSocket client for a second ReStim instance."""
    def __init__(self, status: Callable[[str], None]) -> None:
        self.status = status
        self._socket = None
        self._lock = threading.Lock()

    @property
    def connected(self) -> bool:
        return self._socket is not None

    def connect(self, host: str, port: int) -> None:
        self.disconnect()
        try:
            import websocket
        except ImportError as exc:
            raise OSError("Python package 'websocket-client' is required") from exc
        self._socket = websocket.create_connection(
            f"ws://{host}:{port}/tcode", timeout=2.0,
            http_proxy_host=None, http_proxy_port=None,
        )
        self.status(f"Connected to ws://{host}:{port}/tcode")

    def disconnect(self) -> None:
        with self._lock:
            if self._socket:
                try:
                    self._socket.close()
                except Exception:
                    pass
            self._socket = None
        self.status("Disconnected")

    def send_prostate(self, alpha: float, beta: float, volume: float,
                      frequency: float, pulse_frequency: float,
                      pulse_width: float, pulse_rise_time: float) -> None:
        # This instance's restim.ini explicitly maps carrier frequency to F0.
        message = " ".join((
            format_command("L0", alpha), format_command("L1", beta),
            format_command("V0", volume), format_command("F0", frequency),
            format_command("P0", pulse_frequency), format_command("P1", pulse_width),
            format_command("P3", pulse_rise_time),
        ))
        with self._lock:
            if not self._socket:
                return
            try:
                self._socket.send(message)
            except Exception as exc:
                try:
                    self._socket.close()
                except Exception:
                    pass
                self._socket = None
                self.status(f"Disconnected: {exc}")
