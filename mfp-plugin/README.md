# MultiFunPlayer Timeline Absolute plugin

Companion plugin for Vector 1A. It publishes **absolute media time** from MultiFunPlayer as normal T-code axes so Vector can derive media percent for volume ramp (and Vector live custom events).

This folder is **distribution only**. It is not imported by the Vector Python package.

## Requirements

- MultiFunPlayer **1.32.0 or later** (tested against the 1.32.x plugin compiler)
- Do **not** use `#:plugin` / `#:name` directives with 1.32.x — those are for newer MFP file-based plugins and cause `CS9298` on 1.32.1

## Wire format

| Axis | Meaning | Encode (plugin) | Decode (Vector) |
|------|---------|-----------------|-----------------|
| `T0` | Absolute position | `T0 = clamp(position_seconds / 10000, 0, 1)` | `position_s = T0 * 10000` |
| `T1` | Absolute duration | `T1 = clamp(duration_seconds / 10000, 0, 1)` | `duration_s = T1 * 10000` |

Shared constant: `TIMELINE_SCALE_SECONDS = 10000` (~2 hours 47 minutes). Longer media clamps both axes to `1.0`.

Derived in Vector (not sent on the wire):

- `progress = position_s / duration_s` when `duration_s > 0` (media volume ramp)
- `position_ms = round(position_s * 1000)` (Vector live custom events on volume, pulse, frequency, alpha/beta, and e1–e4)

Standard 4-digit T-code gives about **1 second** position resolution at this scale.

`T0` / `T1` are outside ReStim’s usual authored signature set (`V0`, `C0`, `P*`, `E*`), so Vector auto authored routing should not treat them as a ReStim stream.

## Install (MultiFunPlayer 1.32.x)

1. Copy `TimelineAbsolute/TimelineAbsolute.cs` into your MultiFunPlayer `Plugins` folder.
   - Either `Plugins\TimelineAbsolute.cs`, or `Plugins\TimelineAbsolute\TimelineAbsolute.cs` (one subfolder deep is allowed).
2. Restart MultiFunPlayer, or wait for it to recompile on file change.
3. Enable the **TimelineAbsolute** plugin in MFP if it is listed as disabled.

### Device profile

1. Open MFP **Device** settings.
2. Add custom axes named **`T0`** and **`T1`** (names must match `^[A-Z][0-9]$`) and enable them.
3. On the T-code **output that targets Vector**, include **`T0`** and **`T1`** on the same output as `L0`.

If an axis is missing from the device profile, the plugin logs a one-time warning and skips that axis until it is available.

### Confirm in Vector

1. Start Vector’s MFP listener and play media through an MFP-connected player.
2. Open **MFP axes** diagnostics.
3. Raw packets should list `T0` and `T1`.
4. Decode check: `T0 * 10000` ≈ player position in seconds (±1 s); `T1 * 10000` ≈ media duration when known.

Commission with ReStim’s graphical display and stimulation hardware disconnected.

## Behaviour

- **Play:** `T0` tracks absolute position; `T1` tracks duration once known.
- **Seek:** `T0` jumps immediately to the new absolute time (no media-percent encoding).
- **Pause:** `T0` holds; the plugin does not advance on wall-clock.
- **Unknown duration:** `T0` still updates; `T1` is **not** written until duration is known and > 0.
- **New media / reset / path change:** cached position and duration are cleared; when a new duration arrives, `T0` is set to `0` if no position has been received yet, and `T1` is set from that duration.
- **Clamp:** values at or above 10000 seconds encode as `1.0`.

Optional actions (defaults `T0` / `T1`):

- `TimelineAbsolute::PositionAxis::Set`
- `TimelineAbsolute::DurationAxis::Set`

If you rename axes, configure the matching Vector timeline settings the same way.

## Files

- `TimelineAbsolute/TimelineAbsolute.cs` — plugin source (`PluginBase` class for MFP 1.32+)

## Troubleshooting

| Symptom | Cause | Fix |
|---------|--------|-----|
| `CS9298: '#:' directives can be only used in file-based programs` | Old `#:plugin` header on MFP 1.32.x | Use the current `.cs` file without `#:` lines; re-copy from this repo |
| Axis missing / no T0 in packets | Device profile or output omits T0/T1 | Enable axes and include them on the Vector T-code output |
| T1 shows unknown / never updates | Duration not published after load | Restart MFP with the latest plugin; play media so `Media::Duration` is known |
| T0/T1 stuck at **1** (full scale) | Value treated as ms and clamped at 10000 | Latest plugin normalizes large values; ensure axis defaults are **0.0**, not 1.0 |
| Plugin not listed | Wrong folder depth or compile error | Keep at most one subfolder under `Plugins`; check MFP log |

## Safety

Vector 1A is commissioning software, not a medical device. Do not validate timeline or output behaviour on connected stimulation hardware. Use ReStim’s display with hardware disconnected first.
