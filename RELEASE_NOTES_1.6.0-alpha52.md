# Vector 1A 1.6.0-alpha52

Alpha 52 adds media-ramp time waypoints with funscript import/export, funscript-tools MCB orgasm/goodBoy live events (with duration stretch), a Restim T-code capture proxy, and hardening for MFP packet handling and Restim-original stroke direction cache.

This build is published on the Senorgif33 fork of Vector 1A. Upstream Alpha 51 behaviour is unchanged when the new waypoint and proxy tools stay unused and custom events remain disabled (default).

## Media volume ramp waypoints

Optional **extra time waypoints** target Floor 1–3 / Ceiling 1–3 at absolute media times (`h:mm:ss` / `m:ss`). After the last waypoint, gain holds for the rest of the file; levels may rise or fall between points. Each waypoint can use its own curve for the segment arriving at that point (global Curve remains simple-mode and the default for new points). Floor 2/3 and Ceiling 2/3 controls appear when a waypoint uses that level.

Import/export supports Vector JSON or an OFS volume `.funscript` (dense baked actions for playback, bookmarks at Floor/Ceiling points, plus `vector1a_media_ramp` metadata so the curve editor round-trips).

## MCB orgasm / goodBoy live events

Bundled `event_definitions.yml` now includes:

- `mcb_goodboy` — sibling of `mcb_submit` (27 Hz / width 50 / 5 s)
- `mcb_orgasm_countdown` — pulse/amp/goodBoy ladder; base stroke untouched
- `mcb_orgasm_countdown_stroke_override` — same plus overwrite α/β/e1–e4

Stretching `duration_ms` matches funscript-tools offline Apply: climax (`seg_orgasm_ms`) grows with duration; goodBoy duration scales with climax (minimum 5 s). Place countdown events on the “5” cue; orgasm peak remains at +21 s by default.

## Restim T-code capture proxy

New optional package `restim_tcode_proxy` (launcher `start-tcode-proxy.bat`) sits between Vector and Restim WebSocket `/tcode`, always logging every frame while forwarding to real Restim. Point Vector primary/prostate WS at the proxy listen ports (defaults `13346` / `13350`).

## Hardening

- MFP TCP/UDP listen threads recover from packet-handler errors instead of dying.
- Restim-original stroke direction cache no longer KeyErrors when pruning live high sequence ids.

## Compatibility

- File-based and `EVT` custom events are unchanged in role; new event names require this build’s (or newer) definitions catalog.
- Do not also bake the same events offline into authored funscripts (double apply).
- Optional media volume ramp without waypoints behaves as in Alpha 51.

## Validation

Automated tests cover orgasm countdown stretch/expand, ramp waypoint import/export and gain evaluation, T-code proxy basics, and the prior Alpha 51 suite.
