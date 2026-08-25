# Vector 1A 1.6.0-alpha51

Alpha 51 adds live companion event triggers (`EVT`), Restim sensor suppression (`S1`) through events and authored axes, and tighter Timeline Absolute freshness for 5-digit `T0`/`T1`.

This build is published on the Senorgif33 fork of Vector 1A. Upstream Alpha 50 behaviour is unchanged when custom events remain disabled (default). Authored `.events.yml` playback continues to work alongside the new live triggers.

## Live `EVT` triggers

Companions such as Fap-Hero may send line-oriented triggers on the same MFP TCP/UDP port as L0/`T0`:

```text
EVT name=edge duration_ms=5000
```

Vector schedules each trigger at `receive + look-ahead` and applies effects on the send clock (`sample.due_at`), using the same vendored `event_definitions.yml` catalog as file events. Triggers do **not** use session `T0` for activation. Malformed `EVT` lines are ignored and do not stall the L0 stream.

For Journey / Fap-Hero play, leave the `.events.yml` path empty and keep definitions loaded. Session `T0`/`T1` remain media volume-ramp only.

When both a file and live triggers are active, file steps (media position) apply first, then trigger steps merge in receive order.

## Sensor suppression (`S1`)

Custom events now support `sensor_suppression` (Restim `S1`):

- While events are enabled, Vector seeds `S1` from an authored MFP `S1` axis when routed, otherwise `0.0` (sensors fully active).
- Edge-family definitions raise mute to `0.5` for their windows; cum-family / `ruin` raise it to `1.0`.
- Authored `sensor_suppression` scripts can also pass through alone via Manual `S1` or Auto when a ReStim signature axis is live.

Requires a Restim build that supports sensor suppression. Bundled definitions include the corresponding overwrite steps.

## Timeline Absolute (5-digit precision)

Set the MFP device **Output precision** to **5** so `T0`/`T1` use 5-digit T-code (~0.1 s steps at the 10000 s scale). Freshness is about **1 s**, then a short **2 s** hold for the volume ramp, so quiet packets do not flicker status to “none” or slam gain to the floor. The Timeline Absolute plugin README documents the precision requirement.

## Compatibility

- File-based funscript-tools `.events.yml` on `T0`/`T1` is unchanged in role; definitions remain required for both file and `EVT` paths.
- Optional media volume ramp and standalone Vector generation are unchanged when events stay off.
- Do not also bake the same events offline into authored funscripts (double apply).

## Validation

Automated tests cover `EVT` parse/schedule/overlap, S1 seeding and event mute levels, timeline freshness/hold with 5-digit expectations, routing labels, and the prior Alpha 50 suite.
