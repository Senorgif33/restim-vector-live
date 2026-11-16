from __future__ import annotations

from dataclasses import dataclass
import base64
import ctypes
import html
import json
import os
from pathlib import Path
import re
import urllib.parse
import urllib.request


@dataclass(frozen=True)
class MediaSnapshot:
    player: str
    connected: bool
    state: str = "unknown"
    position_seconds: float | None = None
    duration_seconds: float | None = None
    rate: float = 1.0
    media_path: str | None = None
    title: str | None = None
    error: str | None = None
    raw_position: str | None = None
    raw_duration: str | None = None
    position_source: str | None = None


def _http_text(url: str, *, timeout: float = 0.35, auth: tuple[str, str] | None = None) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Vector1A/1.6"})
    if auth is not None:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode("utf-8")).decode("ascii")
        req.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read().decode("utf-8", errors="replace")


def _file_uri_to_path(uri: str | None) -> str | None:
    if not uri:
        return None
    try:
        parsed = urllib.parse.urlparse(uri)
        if parsed.scheme.lower() != "file":
            return None
        path = urllib.parse.unquote(parsed.path or "")
        if parsed.netloc:
            path = f"//{parsed.netloc}{path}"
        if os.name == "nt" and re.match(r"^/[A-Za-z]:/", path):
            path = path[1:]
        return os.path.normpath(path)
    except Exception:
        return None


def _vlc_current_uri(node: object, current_id: str | int | None) -> str | None:
    if isinstance(node, dict):
        if current_id is not None and str(node.get("id")) == str(current_id) and node.get("uri"):
            return str(node.get("uri"))
        if str(node.get("current", "")).lower() == "current" and node.get("uri"):
            return str(node.get("uri"))
        children = node.get("children")
        if isinstance(children, list):
            for child in children:
                found = _vlc_current_uri(child, current_id)
                if found:
                    return found
    elif isinstance(node, list):
        for item in node:
            found = _vlc_current_uri(item, current_id)
            if found:
                return found
    return None


def poll_vlc(host: str = "127.0.0.1", port: int = 8080, password: str = "") -> MediaSnapshot:
    base = f"http://{host}:{int(port)}"
    try:
        status = json.loads(_http_text(base + "/requests/status.json", auth=("", password)))
        state = str(status.get("state") or "unknown")
        pos = float(status["time"]) if status.get("time") is not None else None
        dur = float(status["length"]) if status.get("length") is not None else None
        rate = float(status.get("rate") or 1.0)
        title = None
        media_path = None
        info = status.get("information") if isinstance(status, dict) else None
        if isinstance(info, dict):
            category = info.get("category")
            if isinstance(category, dict):
                meta = category.get("meta")
                if isinstance(meta, dict):
                    title = str(meta.get("title") or meta.get("filename") or "") or None
                    raw_path = meta.get("path") or meta.get("filename")
                    if raw_path and os.path.isabs(str(raw_path)):
                        media_path = os.path.normpath(str(raw_path))
        try:
            playlist = json.loads(_http_text(base + "/requests/playlist.json", auth=("", password)))
            uri = _vlc_current_uri(playlist, status.get("currentplid"))
            media_path = _file_uri_to_path(uri) or media_path
        except Exception:
            pass
        if not title and media_path:
            title = os.path.basename(media_path)
        return MediaSnapshot("VLC", True, state, pos, dur, rate, media_path, title)
    except Exception as exc:
        return MediaSnapshot("VLC", False, error=str(exc))


def _parse_clock_value(text: str | None) -> float | None:
    if text is None:
        return None
    s = html.unescape(re.sub(r"<[^>]*>", "", str(text))).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass
    parts = s.split(":")
    if len(parts) in (2, 3):
        try:
            nums = [float(x) for x in parts]
            if len(nums) == 2:
                return nums[0] * 60.0 + nums[1]
            return nums[0] * 3600.0 + nums[1] * 60.0 + nums[2]
        except ValueError:
            return None
    return None


def _extract_mpc_value(source: str, key: str) -> str | None:
    patterns = [
        rf'<[^>]+(?:id|name)=["\']{re.escape(key)}["\'][^>]*>(.*?)</[^>]+>',
        rf'<!--\s*{re.escape(key)}\s*-->(.*?)<!--\s*/{re.escape(key)}\s*-->',
        rf'\b{re.escape(key)}\b\s*[:=]\s*["\']?([^\r\n<"\']+)',
    ]
    for pattern in patterns:
        m = re.search(pattern, source, flags=re.IGNORECASE | re.DOTALL)
        if m:
            return html.unescape(re.sub(r"<[^>]*>", "", m.group(1))).strip()
    return None


def _mpc_milliseconds(value: str | None) -> float | None:
    """Parse MPC's numeric position/duration variables, which are milliseconds."""
    if value is None:
        return None
    try:
        return float(str(value).strip()) / 1000.0
    except (TypeError, ValueError):
        return None


def _normalize_mpc_state(state_value: str | None, state_string: str | None) -> str:
    # MPC's variables page exposes a numeric state. In current MPC-HC/BE builds
    # 0=stopped/closed, 1=paused, 2=playing. Prefer it over statestring because
    # statestring is localized on non-English installations.
    try:
        code = int(str(state_value).strip()) if state_value is not None else None
    except (TypeError, ValueError):
        code = None
    if code == 2:
        return "playing"
    if code == 1:
        return "paused"
    if code == 0:
        return "stopped"
    text = str(state_string or state_value or "unknown").strip().lower()
    if text in {"play", "playing"}:
        return "playing"
    if text in {"pause", "paused"}:
        return "paused"
    if text in {"stop", "stopped", "closed"}:
        return "stopped"
    return text or "unknown"


def poll_mpc(host: str = "127.0.0.1", port: int = 13579) -> MediaSnapshot:
    base = f"http://{host}:{int(port)}"
    try:
        source = _http_text(base + "/variables.html")
        path = (_extract_mpc_value(source, "filepath") or
                _extract_mpc_value(source, "file") or
                _extract_mpc_value(source, "filename"))
        if path:
            path = urllib.parse.unquote(path.strip())
            if path.lower().startswith("file://"):
                path = _file_uri_to_path(path)
            elif not os.path.isabs(path):
                folder = _extract_mpc_value(source, "filedir")
                if folder:
                    path = os.path.join(folder, path)
            if path:
                path = os.path.normpath(path)

        raw_pos = _extract_mpc_value(source, "position")
        raw_dur = _extract_mpc_value(source, "duration")
        raw_pos_string = _extract_mpc_value(source, "positionstring")
        raw_dur_string = _extract_mpc_value(source, "durationstring")

        # Critical detail: MPC's numeric <position> and <duration> variables are
        # milliseconds, while positionstring/durationstring are whole-second clock
        # strings. Prefer numeric milliseconds for precision, with strings as fallback.
        pos = _mpc_milliseconds(raw_pos)
        position_source = "position_ms"
        if pos is None:
            pos = _parse_clock_value(raw_pos_string)
            position_source = "positionstring" if pos is not None else None
        dur = _mpc_milliseconds(raw_dur)
        if dur is None:
            dur = _parse_clock_value(raw_dur_string)

        state_value = _extract_mpc_value(source, "state")
        state_string = _extract_mpc_value(source, "statestring")
        state = _normalize_mpc_state(state_value, state_string)
        rate_text = _extract_mpc_value(source, "playbackrate")
        try:
            rate = float(rate_text) if rate_text else 1.0
        except ValueError:
            rate = 1.0
        title = os.path.basename(path) if path else (_extract_mpc_value(source, "filename") or None)
        return MediaSnapshot("MPC", True, state, pos, dur, rate, path, title,
                             raw_position=raw_pos, raw_duration=raw_dur,
                             position_source=position_source)
    except Exception as exc:
        return MediaSnapshot("MPC", False, error=str(exc))


_POT_GET_TOTAL_TIME = 0x5002
_POT_GET_CURRENT_TIME = 0x5004
_POT_GET_PLAY_STATUS = 0x5006
_POT_GET_FILENAME = 0x6020
_WM_USER = 0x0400
_WM_COPYDATA = 0x004A


def _potplayer_snapshot(
    position_ms: int, duration_ms: int, state_code: int, media_path: str | None,
) -> MediaSnapshot:
    """Map PotPlayer window-message results into a media snapshot.

    Play status is 0 stopped, 1 paused, 2 playing. ``-1`` means nothing is loaded.
    Times are milliseconds. A non-positive duration is omitted so it cannot clamp
    position to zero.
    """
    if state_code < 0:
        return MediaSnapshot("PotPlayer", False, state="stopped", error="no media")
    state = {0: "stopped", 1: "paused", 2: "playing"}.get(int(state_code), "unknown")
    pos = float(position_ms) / 1000.0 if position_ms >= 0 else None
    dur = float(duration_ms) / 1000.0 if duration_ms > 0 else None
    path = (str(media_path).strip() or None) if media_path else None
    title = os.path.basename(path) if path else None
    return MediaSnapshot(
        "PotPlayer", pos is not None, state, pos, dur, 1.0, path, title,
        raw_position=str(position_ms), raw_duration=str(duration_ms),
        position_source="position_ms")


def poll_potplayer() -> MediaSnapshot:
    """Read PotPlayer position, duration, state, and path via window messages."""
    if os.name != "nt":
        return MediaSnapshot("PotPlayer", False, error="PotPlayer clock requires Windows")
    try:
        queried = _query_potplayer()
    except OSError as exc:
        return MediaSnapshot("PotPlayer", False, error=str(exc))
    if queried is None:
        return MediaSnapshot("PotPlayer", False, error="PotPlayer window not found")
    position_ms, duration_ms, state_code, media_path = queried
    return _potplayer_snapshot(position_ms, duration_ms, state_code, media_path)


def _potplayer_user32():
    user32 = ctypes.windll.user32
    user32.SendMessageW.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    user32.DefWindowProcW.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    return user32


def _query_potplayer() -> tuple[int, int, int, str | None] | None:
    user32 = _potplayer_user32()
    hwnd = user32.FindWindowW("PotPlayer64", None) or user32.FindWindowW("PotPlayer", None)
    if not hwnd:
        return None
    position_ms = int(user32.SendMessageW(hwnd, _WM_USER, _POT_GET_CURRENT_TIME, 0))
    duration_ms = int(user32.SendMessageW(hwnd, _WM_USER, _POT_GET_TOTAL_TIME, 0))
    state_code = int(user32.SendMessageW(hwnd, _WM_USER, _POT_GET_PLAY_STATUS, 0))
    path = _potplayer_filename(user32, hwnd)
    return position_ms, duration_ms, state_code, path


_potplayer_path_box: dict[str, str] = {}
_WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t)


@_WNDPROC
def _potplayer_wndproc(hwnd, msg, wparam, lparam):
    if msg == _WM_COPYDATA and lparam:
        data = ctypes.cast(lparam, ctypes.POINTER(_COPYDATASTRUCT)).contents
        if int(data.dwData or 0) == _POT_GET_FILENAME and data.lpData and data.cbData:
            raw = ctypes.string_at(data.lpData, int(data.cbData))
            text = _decode_potplayer_path(raw)
            if text:
                _potplayer_path_box["path"] = text
        return 1
    return _potplayer_user32().DefWindowProcW(hwnd, msg, wparam, lparam)


def _potplayer_filename(user32, pot_hwnd: int) -> str | None:
    """Ask PotPlayer for the media path. It replies with WM_COPYDATA."""
    _potplayer_path_box.clear()
    listener = _potplayer_listener(user32)
    if not listener:
        return None
    try:
        user32.SendMessageW(pot_hwnd, _WM_USER, _POT_GET_FILENAME, int(listener))
    finally:
        user32.DestroyWindow(listener)
    return _potplayer_path_box.get("path")


class _COPYDATASTRUCT(ctypes.Structure):
    _fields_ = [
        ("dwData", ctypes.c_void_p),
        ("cbData", ctypes.c_uint32),
        ("lpData", ctypes.c_void_p),
    ]


def _decode_potplayer_path(raw: bytes) -> str | None:
    if len(raw) >= 4 and raw[1] == 0 and raw[3] == 0:
        text = raw.decode("utf-16-le", errors="replace")
    else:
        text = raw.decode("utf-8", errors="replace")
    text = text.split("\x00", 1)[0].strip()
    return text or None


_potplayer_class_ready = False


def _potplayer_listener(user32) -> int:
    global _potplayer_class_ready
    class_name = "Vector1APotPlayerClock"
    if not _potplayer_class_ready:
        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", ctypes.c_uint),
                ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", ctypes.c_void_p),
                ("hIcon", ctypes.c_void_p),
                ("hCursor", ctypes.c_void_p),
                ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", ctypes.c_wchar_p),
                ("lpszClassName", ctypes.c_wchar_p),
            ]
        wndclass = WNDCLASSW()
        wndclass.lpfnWndProc = ctypes.cast(_potplayer_wndproc, ctypes.c_void_p)
        wndclass.lpszClassName = class_name
        atom = user32.RegisterClassW(ctypes.byref(wndclass))
        if not atom and ctypes.get_last_error() not in (0, 1410):
            return 0
        _potplayer_class_ready = True
        _potplayer_listener._wndclass = wndclass
    hwnd_message = ctypes.c_void_p(-3)
    user32.CreateWindowExW.argtypes = [
        ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    user32.CreateWindowExW.restype = ctypes.c_void_p
    hwnd = user32.CreateWindowExW(
        0, class_name, "Vector1A", 0, 0, 0, 0, 0, hwnd_message, None, None, None)
    return int(hwnd or 0)


def _find_matching_named_files(
    media_path: str | None,
    exact_names: tuple[str, ...] | list[str],
    library_dirs: list[str] | tuple[str, ...] = (),
) -> str | None:
    if not media_path or not exact_names:
        return None
    media = Path(media_path)
    names = tuple(exact_names)
    candidates: list[Path] = []
    if media.parent:
        for name in names:
            candidates.append(media.parent / name)
    for folder in library_dirs:
        if not folder:
            continue
        root = Path(os.path.expanduser(folder))
        for name in names:
            candidates.append(root / name)
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    for folder in library_dirs:
        if not folder:
            continue
        root = Path(os.path.expanduser(folder))
        if not root.is_dir():
            continue
        try:
            for name in names:
                for match in root.rglob(name):
                    if match.is_file():
                        return str(match.resolve())
        except OSError:
            continue
    return None


def find_matching_funscript(media_path: str | None, library_dirs: list[str] | tuple[str, ...] = ()) -> str | None:
    if not media_path:
        return None
    return _find_matching_named_files(
        media_path, (Path(media_path).stem + ".funscript",), library_dirs)


def find_matching_events_file(
    media_path: str | None,
    library_dirs: list[str] | tuple[str, ...] = (),
) -> str | None:
    """Resolve ``{stem}.events.yml`` / ``.events.yaml`` beside media or in libraries."""
    if not media_path:
        return None
    stem = Path(media_path).stem
    return _find_matching_named_files(
        media_path, (f"{stem}.events.yml", f"{stem}.events.yaml"), library_dirs)


def find_matching_ramp_file(
    media_path: str | None,
    library_dirs: list[str] | tuple[str, ...] = (),
) -> str | None:
    """Resolve Vector volume-ramp sidecar (not the L0 ``{stem}.funscript``)."""
    if not media_path:
        return None
    stem = Path(media_path).stem
    return _find_matching_named_files(
        media_path,
        (f"{stem}.ramp.funscript", f"{stem}.ramp.json"),
        library_dirs,
    )


def events_auto_load_decision(
    media_path: str | None,
    last_handled_media: str | None,
    current_events_path: str,
    matched_events_path: str | None,
) -> tuple[bool, str | None, bool]:
    """Decide whether to load/clear events for a new media path.

    Returns ``(media_changed, events_path_to_apply, should_warn)``:
    - ``media_changed`` False when media is ``None`` (no PATH yet) or unchanged.
    - ``events_path_to_apply`` is a path to load, ``""`` to clear, or ``None``
      when the current events path is already correct.
    - ``should_warn`` is True only when media changed and no match was found.
    """
    if media_path is None or media_path == last_handled_media:
        return False, None, False
    if matched_events_path:
        current = (current_events_path or "").strip()
        try:
            same = (
                bool(current)
                and Path(current).resolve() == Path(matched_events_path).resolve()
            )
        except OSError:
            same = current == matched_events_path
        if same:
            return True, None, False
        return True, matched_events_path, False
    return True, "", True


MISSING_FILE_WARN_COOLDOWN_SECONDS = 30.0


def missing_file_warn_due(
    last_warn_at: float | None,
    now: float,
    cooldown_seconds: float = MISSING_FILE_WARN_COOLDOWN_SECONDS,
) -> bool:
    """True when another missing-file dialog may be shown.

    The first warning is always due. Later ones wait until ``cooldown_seconds``
    have passed, so a flapping media path cannot stack dialogs.
    """
    if last_warn_at is None:
        return True
    return (now - last_warn_at) >= cooldown_seconds
