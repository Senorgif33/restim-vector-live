# Vector 1A 1.6.0-alpha54

Alpha 54 adds an embedded LAN control API for mobile remotes and an optional dark UI theme.

This build is published on the Senorgif33 fork of Vector 1A. Upstream Alpha 53 behaviour is unchanged when the control API stays disabled (default).

## LAN control API

Optional HTTP + WebSocket server inside the Vector process (default bind `0.0.0.0:8787`):

- `GET /v1/schema` — panels, writable fields, actions
- `GET /v1/state` / `POST /v1/state` — full snapshot and partial settings patch
- `WS /v1/stream` — live snapshot + deltas
- `POST /v1/actions/{name}` — start / stop / neutral / resume and related actions

Desktop-only paths (launch targets, event file paths, four-phase host/port) are excluded from remote writes. Enable and bind from the new **Remote control** section in the UI.

## Dark mode

Optional **Dark mode** toggle applies a clam-based dark theme across panels, canvases, and meters. Preference is saved with other settings.

## Compatibility

- Control API off by default; no change to MFP/Restim motion path when unused.
- Companion remotes should match the shared `/v1` contract used by Restim-vector-live-remote-control.

## Validation

Automated tests cover schema/state/patch/actions HTTP routes, WebSocket accept framing, and writable-field filtering for desktop-only keys.
