"""Listen as Restim /tcode, capture every Vector frame, forward to real Restim."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .ws import (
    OP_CLOSE,
    OP_PING,
    OP_PONG,
    OP_TEXT,
    connect_upstream,
    encode_frame,
    encode_text,
    parse_http_headers,
    server_handshake_response,
    try_decode_frame,
)


StatusCallback = Callable[[str], None]
LineCallback = Callable[[str], None]


def default_log_dir() -> Path:
    local = Path.home() / "AppData" / "Local" / "Vector1A" / "tcode-proxy"
    if os_name_is_windows():
        return local
    return Path.home() / ".local" / "share" / "Vector1A" / "tcode-proxy"


def os_name_is_windows() -> bool:
    import sys
    return sys.platform.startswith("win")


@dataclass(frozen=True)
class ChannelConfig:
    name: str
    listen_host: str
    listen_port: int
    upstream_host: str
    upstream_port: int
    enabled: bool = True


class CaptureLog:
    """Append-only session log; every write is flushed immediately."""

    def __init__(self, path: Path, on_line: LineCallback | None = None) -> None:
        self.path = path
        self._on_line = on_line
        self._lock = threading.Lock()
        self._closed = False
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("a", encoding="utf-8", newline="\n")
        self.write_meta(f"session_start path={path}")

    def write_meta(self, text: str) -> None:
        self._write("META", "-", text)

    def write_frame(self, channel: str, direction: str, payload: str) -> None:
        self._write(channel, direction, payload)

    def _write(self, channel: str, direction: str, payload: str) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        line = f"{stamp}  {channel:<8}  {direction:<3}  {payload}"
        with self._lock:
            if self._closed:
                return
            try:
                self._file.write(line + "\n")
                self._file.flush()
            except Exception:
                return
        if self._on_line:
            try:
                self._on_line(line)
            except Exception:
                pass

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                line = f"{stamp}  META      -    session_end"
                self._file.write(line + "\n")
                self._file.flush()
            except Exception:
                pass
            try:
                self._file.close()
            except Exception:
                pass
            self._closed = True


class ProxyChannel:
    """One listen port that proxies /tcode to one Restim instance."""

    def __init__(
        self,
        config: ChannelConfig,
        log: CaptureLog,
        status: StatusCallback | None = None,
    ) -> None:
        self.config = config
        self.log = log
        self.status = status or (lambda _text: None)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._listen_sock: socket.socket | None = None
        self._session_lock = threading.Lock()
        self._active: list[socket.socket] = []
        self._session_threads: list[threading.Thread] = []

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._serve, name=f"proxy-{self.config.name}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._listen_sock:
            try:
                self._listen_sock.close()
            except OSError:
                pass
        with self._session_lock:
            for sock in list(self._active):
                try:
                    sock.close()
                except OSError:
                    pass
            self._active.clear()
            sessions = list(self._session_threads)
            self._session_threads.clear()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        for thread in sessions:
            if thread is not threading.current_thread():
                thread.join(timeout=2.0)
        self.status(f"{self.config.name}: stopped")

    def _set_status(self, text: str) -> None:
        self.status(f"{self.config.name}: {text}")

    def _serve(self) -> None:
        cfg = self.config
        try:
            listen = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listen.bind((cfg.listen_host, cfg.listen_port))
            listen.listen(8)
            listen.settimeout(0.5)
            self._listen_sock = listen
        except OSError as exc:
            self._set_status(f"listen failed on {cfg.listen_host}:{cfg.listen_port}: {exc}")
            self.log.write_meta(
                f"{cfg.name} listen_failed {cfg.listen_host}:{cfg.listen_port} {exc}")
            return
        self._set_status(
            f"listening on ws://{cfg.listen_host}:{cfg.listen_port}/tcode "
            f"→ {cfg.upstream_host}:{cfg.upstream_port}")
        self.log.write_meta(
            f"{cfg.name} listening {cfg.listen_host}:{cfg.listen_port} "
            f"upstream {cfg.upstream_host}:{cfg.upstream_port}")
        while not self._stop.is_set():
            try:
                client, addr = listen.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            thread = threading.Thread(
                target=self._handle_client_safe,
                args=(client, addr),
                name=f"proxy-{cfg.name}-session",
                daemon=True,
            )
            with self._session_lock:
                self._session_threads = [
                    item for item in self._session_threads if item.is_alive()]
                self._session_threads.append(thread)
            thread.start()
        try:
            listen.close()
        except OSError:
            pass

    def _track(self, *socks: socket.socket) -> None:
        with self._session_lock:
            self._active.extend(socks)

    def _untrack(self, *socks: socket.socket) -> None:
        with self._session_lock:
            for sock in socks:
                if sock in self._active:
                    self._active.remove(sock)

    def _handle_client_safe(self, client: socket.socket, addr) -> None:
        try:
            self._handle_client(client, addr)
        except Exception as exc:
            try:
                self._set_status(f"session error: {exc}")
                self.log.write_meta(f"{self.config.name} session_crash {exc}")
            except Exception:
                pass
            try:
                client.close()
            except OSError:
                pass

    def _handle_client(self, client: socket.socket, addr) -> None:
        upstream: socket.socket | None = None
        cfg = self.config
        try:
            try:
                client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            client.settimeout(5.0)
            request = b""
            while b"\r\n\r\n" not in request and len(request) < 16384:
                chunk = client.recv(4096)
                if not chunk:
                    return
                request += chunk
            request_line, headers = parse_http_headers(request)
            parts = request_line.split()
            path = parts[1] if len(parts) >= 2 else ""
            if not path.startswith("/tcode"):
                client.sendall(b"HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n")
                self.log.write_meta(f"{cfg.name} rejected path={path} from {addr}")
                return
            key = headers.get("sec-websocket-key")
            if not key:
                client.sendall(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
                return

            # Connect Restim first so Vector never sees a false "connected" flap.
            try:
                upstream = connect_upstream(cfg.upstream_host, cfg.upstream_port)
                try:
                    upstream.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except OSError:
                    pass
            except OSError as exc:
                try:
                    client.sendall(
                        b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n"
                        b"Content-Type: text/plain\r\n\r\n"
                        + f"upstream {cfg.upstream_host}:{cfg.upstream_port}: {exc}".encode()
                    )
                except OSError:
                    pass
                self._set_status(
                    f"Restim not reachable at {cfg.upstream_host}:{cfg.upstream_port} ({exc})")
                self.log.write_meta(f"{cfg.name} upstream_failed {exc}")
                return

            client.sendall(server_handshake_response(key))
            self._set_status(
                f"proxying {addr[0]}:{addr[1]} → "
                f"{cfg.upstream_host}:{cfg.upstream_port}")
            self.log.write_meta(
                f"{cfg.name} vector_connected {addr[0]}:{addr[1]} "
                f"upstream {cfg.upstream_host}:{cfg.upstream_port}")

            client.settimeout(None)
            self._track(client, upstream)
            self._relay(client, upstream)
        except OSError as exc:
            self._set_status(f"session error: {exc}")
            self.log.write_meta(f"{cfg.name} session_error {exc}")
        finally:
            self.log.write_meta(f"{cfg.name} session_closed")
            for sock in (client, upstream):
                if sock is None:
                    continue
                try:
                    sock.close()
                except OSError:
                    pass
            if upstream is not None:
                self._untrack(client, upstream)
            else:
                self._untrack(client)
            if not self._stop.is_set():
                self._set_status(
                    f"listening on ws://{cfg.listen_host}:{cfg.listen_port}/tcode "
                    f"→ {cfg.upstream_host}:{cfg.upstream_port}")

    def _relay(self, client: socket.socket, upstream: socket.socket) -> None:
        stop = threading.Event()
        send_locks = {id(client): threading.Lock(), id(upstream): threading.Lock()}

        def send_on(sock: socket.socket, data: bytes) -> None:
            with send_locks[id(sock)]:
                sock.sendall(data)

        def pump(source: socket.socket, dest: socket.socket, direction: str) -> None:
            buffer = bytearray()
            try:
                while not stop.is_set() and not self._stop.is_set():
                    chunk = source.recv(4096)
                    if not chunk:
                        self.log.write_meta(f"{self.config.name} eof {direction}")
                        break
                    buffer.extend(chunk)
                    while True:
                        frame, consumed = try_decode_frame(buffer)
                        if frame is None:
                            break
                        del buffer[:consumed]
                        self._handle_frame(frame, source, dest, direction, send_on)
            except OSError as exc:
                self.log.write_meta(f"{self.config.name} relay_error {direction} {exc}")
            finally:
                stop.set()
                for sock in (source, dest):
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

        to_upstream = threading.Thread(
            target=pump, args=(client, upstream, "IN"), daemon=True)
        to_client = threading.Thread(
            target=pump, args=(upstream, client, "OUT"), daemon=True)
        to_upstream.start()
        to_client.start()
        while not stop.wait(0.2):
            if self._stop.is_set():
                stop.set()
                break
        to_upstream.join(timeout=1.0)
        to_client.join(timeout=1.0)

    def _handle_frame(self, frame, source: socket.socket, dest: socket.socket,
                      direction: str, send_on) -> None:
        cfg = self.config
        if frame.opcode == OP_TEXT:
            text = frame.payload.decode("utf-8", errors="replace")
            self.log.write_frame(cfg.name, direction, text)
            if direction == "IN":
                # Vector → Restim (client frames must be masked).
                send_on(dest, encode_text(text, mask=True))
            # Restim → Vector is logged only. Vector never reads the socket,
            # so pushing bytes there only confuses its disconnect monitor.
            return
        if frame.opcode == OP_PING:
            # Answer on the same hop. Forwarding Restim pings to Vector caused
            # Restim to drop us (no pong) and Vector to flap with WinError 10054.
            reply_masked = direction == "OUT"  # we are client toward Restim
            send_on(source, encode_frame(OP_PONG, frame.payload, mask=reply_masked))
            self.log.write_meta(f"{cfg.name} ping→pong {direction}")
            return
        if frame.opcode == OP_PONG:
            return
        if frame.opcode == OP_CLOSE:
            self.log.write_meta(f"{cfg.name} websocket_close {direction}")
            raise OSError(f"WebSocket close ({direction})")
        self.log.write_meta(f"{cfg.name} drop_opcode={frame.opcode} {direction}")


class ProxyService:
    def __init__(
        self,
        channels: list[ChannelConfig],
        log_dir: Path | None = None,
        on_status: StatusCallback | None = None,
        on_line: LineCallback | None = None,
    ) -> None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.log_dir = log_dir or default_log_dir()
        self.log_path = self.log_dir / f"session-{stamp}.log"
        self.log = CaptureLog(self.log_path, on_line=on_line)
        self._on_status = on_status or (lambda _text: None)
        self._channels = [
            ProxyChannel(cfg, self.log, status=self._on_status)
            for cfg in channels if cfg.enabled
        ]

    def start(self) -> Path:
        for channel in self._channels:
            channel.start()
        return self.log_path

    def stop(self) -> None:
        for channel in self._channels:
            channel.stop()
        self.log.close()
