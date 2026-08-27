# Restim T-code capture proxy

Standalone transparent WebSocket proxy that sits between **Vector** and
**Restim**. It accepts Vector’s `/tcode` connection, **always appends every
frame to a session log file**, then forwards the same payload to real Restim.

## Quick use

1. Start primary (and optional prostate) ReStim with WebSocket `/tcode` on the
   usual ports (`12346` / `12350`).
2. Double-click `start-tcode-proxy.bat` (or `python -m restim_tcode_proxy`).
3. Click **Start proxy**.
4. In Vector, point:
   - Primary WS → `127.0.0.1:13346`
   - Prostate WS → `127.0.0.1:13350`
5. Run a session. Every frame is flushed to disk under
   `%LOCALAPPDATA%\Vector1A\tcode-proxy\session-YYYYMMDD-HHMMSS.log`.

## Defaults

| Channel  | Listen (Vector connects here) | Upstream Restim |
|----------|-------------------------------|-----------------|
| Primary  | `127.0.0.1:13346`             | `127.0.0.1:12346` |
| Prostate | `127.0.0.1:13350`             | `127.0.0.1:12350` |

Ports are editable in the UI.

## Log format

```
2026-08-25 13:50:01.123  Primary   IN   L01000 L12000 E1... V0...
2026-08-25 13:50:01.124  Prostate  IN   L01000 L12000 V0...
2026-08-25 13:50:01.200  META      -    Primary vector_connected 127.0.0.1:54321
```

- `IN` = Vector → proxy → Restim (what you care about for intensity bumps)
- `OUT` = Restim → Vector (rare; still recorded)
- File writes are flushed on every line; the live window is only a viewer

## Notes

- No third-party dependencies (stdlib + Tkinter).
- Restim must already be listening on the upstream ports before Vector
  connects through the proxy.
- Stopping the proxy closes active sessions; Vector will reconnect when the
  proxy is started again.
