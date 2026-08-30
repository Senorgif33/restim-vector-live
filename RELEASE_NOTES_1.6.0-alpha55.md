# Vector 1A 1.6.0-alpha55

Alpha 55 adds L0 beat-sync extract events, hardens Restim reconnect so a dead peer cannot stall the send path, and makes the LAN control API cheaper when idle.

This build is published on the Senorgif33 fork of Vector 1A. Upstream Alpha 54 behaviour is unchanged when beat-sync events are unused and the control API stays disabled (default).

## Beat-sync MCB extract

New siblings of the Alpha 53 extract composites:

- `mcb_extract_beat` — same 3P pulse/amp/S1 shell and hub–spoke geometry as `mcb_extract`
- `mcb_extract_4p_beat` — same 4P shell as `mcb_extract_4p`

Pole dwells advance every 2nd L0 turnaround from a companion funscript next to the events file (same name rules as funscript-tools), or from `params.switch_offsets_ms`. If neither is available, timing falls back to the SCD ~dur schedule. S1 policy matches the non-beat extract events (50% for full duration).

## Control API responsiveness

- Toolbar **Remote API** toggle (same setting as Remote control section)
- UI-thread state cache so HTTP/WS readers do not marshal onto the Tcl/engine path every poll
- Idle WebSocket broadcast skips `control_state` when no clients are connected

## Restim send-path hardening

Reconnect no longer runs under the send lock. `send` / `send_prostate` fail fast when the socket is down; background monitor reconnects without blocking primary output on connect timeouts.

## Compatibility

- Existing `mcb_extract` / `mcb_extract_4p` SCD timing is unchanged.
- Control API remains off by default.

## Validation

Automated tests cover beat detection / switch segments, beat extract expand, idle control-API broadcast, and fail-fast Restim send when disconnected.
