# Vector 1A 1.6.0-alpha79

Alpha79 is the Senorgif33 fork build on upstream **1.6.0-alpha78**. Alpha78's funscript timeline, Director API, WebSocket MFP input, spatial focus, and event runtime stay. The items below are what this build adds.

Commission with ReStim's graphical display and stimulation hardware disconnected. Vector 1A is not a medical device.

## Downloads

- `Vector1A-1.6.0-alpha79-windows-exe.zip` — Windows app. No Python required. The folder contains `Vector1A-1.6.0-alpha79.exe` plus editable `event_definitions.yml` and `director_event_allowlist.yml`. The exe reads those files when they sit beside it. A definitions path set in the Events panel still overrides `event_definitions.yml`.
- `Vector1A-1.6.0-alpha79-windows.zip` — Python source. Requires Python 3.11 or newer with Tkinter. Run `start-vector1a.bat`.

Existing settings load from `%LOCALAPPDATA%\Vector1A\settings.json`. New settings use defaults when absent.

## Output

- A fresh install calculates at **120 Hz**. A saved **Points per second** value is kept.
- **Restim I-suffix (smooth viz)** is off by default. When it is on, position axes `L0`, `L1`, and `E1`–`E4` get a sample-period `I` suffix (`I8` at 120 Hz). Volume and pulse axes do not. Reconnect neutrals do not.
- `T0` and `T1` are not a media clock. If MultiFunPlayer still sends them, Vector does not forward them to ReStim.

## Prostate

- **Stroke source** is **Follow primary L0** or **MFP axis**. The axis name defaults to `L3`.
- **Follow primary L0** copies the primary stroke and the primary pulse. The extra prostate motion controls stay hidden.
- **MFP axis** drives prostate stroke from that axis. It does not fall back to `L0`. The axis is not forwarded to ReStim.
- While an MFP axis is selected, prostate motion has its own mode, variation, spatial response, reversal boost, and stroke-phase texture. `P0`, `P1`, and `P3` are calculated from the prostate script through the shared pulse limits.
- Prostate `F0` remains the same carrier command as primary `C0`.

## Media clock

Custom-event file steps and the media volume ramp use **VLC**, **MPC**, or **PotPlayer**. **Auto media player** tries VLC, then MPC, then PotPlayer.

**MFP pattern sync** and **Manual preview** do not drive events or the ramp. The funscript timeline still does not replace incoming MFP motion.

PotPlayer is read on Windows by window message. VLC stays `127.0.0.1:8080`. MPC stays `127.0.0.1:13579`.

## Sidecar auto-load

Vector reads a UDP `PATH` line on the MFP listener (default `127.0.0.1:12345`). `PATH` is not T-code. Events and ramp auto-load use that media path. Funscript auto-load still uses the player path.

- **Auto-load matching events file** (default on) loads `<video stem>.events.yml` or `.events.yaml` beside that path, then the funscript script libraries. No match clears the previous events file. A missing-file warning shows at most once every 30 seconds. No `PATH` yet leaves auto-load idle. An empty `PATH` clears a leftover file.
- **Auto-load matching ramp file** (default on) loads `<video stem>.ramp.funscript` or `.ramp.json`. That is not the L0 `.funscript`. No match clears leftover waypoints. A warning shows only when a ramp was actually loaded.

## Media volume ramp

Optional. Gain follows player progress between floor and ceiling (0.0–1.0). **Use extra time waypoints** adds timed levels (`floor1`–`floor3`, `ceiling1`–`ceiling3`) and a curve per segment. Import and export use `.ramp.funscript` (OFS bookmarks plus Vector metadata) or `.ramp.json`.

## Custom events

**Enable custom events** is off by default and separate from Director events. File steps use player `position_ms`.

An `EVT name=… key=value…` line on the MFP listener schedules that recipe on the same look-ahead clock as L0. It is ignored unless custom events are on.

Beat-sync extract (`mcb_extract_beat`, `mcb_extract_4p_beat`) minimum duration is **25 seconds**. SCD extract (`mcb_extract`, `mcb_extract_4p`) minimum duration is **60 seconds**.

**Auto event volume headroom** is on by default. It takes the largest upward additive volume delta of any one event, ignores `test_slaps` and overwrite steps, and clamps at **0.50**. Overlapping events are not added together. With custom events off and Director events on, the scan uses the Director allowlist. Clear the checkbox to set the value by hand (0.00–0.50). Post-ramp volume is multiplied by `1 - headroom` before events apply.

## Director custom events

**Allow curated Director custom events** stays off until you enable it. The catalogue is every bundled recipe except `tranquil` and `test_slaps`. Companion duration is **2–600 seconds**. Reduced amplitudes for the stronger volume recipes are in `director_event_allowlist.yml`. Cancel, Neutral, and Stop still clear active events.

## Send capture

**Custom events → Capture send log** writes one flushed line per primary Restim send to `%LOCALAPPDATA%\Vector1A\send-capture\`. Each line is stamped with player `position_ms` (`media_ms=-` when the player position is missing) and includes final primary `V0`, `P0`, `P1`, `S1`, and post-event prostate volume `Vp`. Media time is recorded even when custom events are off. A pause repeats the same `media_ms`.

## Remote control API

Toolbar **Remote API** is off by default. It is separate from the loopback Director API. There is no authentication. Default bind is `0.0.0.0:8787`. Leave it off except on a trusted LAN.

- `GET /v1/schema`
- `GET /v1/state`
- `POST /v1/state`
- `POST /v1/actions/{name}`
- `WS /v1/stream`

Actions include neutral, stop, resume, listener start/stop, Restim connect and disconnect, and preset apply, capture, and A/B toggle.
