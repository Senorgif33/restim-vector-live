# Vector 1A 1.6.0-alpha50

Alpha 50 adds an optional absolute media timeline from MultiFunPlayer, a media-percent volume ramp, and live funscript-tools custom events on Vector’s delayed send clock.

This build is published on the Senorgif33 fork of Vector 1A. Upstream Alpha 49 behaviour is unchanged when timeline, ramp, and events remain disabled (defaults).

## Media timeline (`T0` / `T1`)

Vector can decode absolute media position and duration from MFP axes **T0** / **T1** (scale 10000 s). Timeline axes are never forwarded to ReStim.

Companion MultiFunPlayer plugin: `mfp-plugin/` (**Timeline Absolute**). Ordinary 4-digit T-code often updates `T0` only about once per media second; Vector keeps the last known time live for a few seconds and briefly holds it for the volume ramp so sparse packets do not flicker status to “none” or slam gain to the floor.

## Media volume ramp

Optional **Media volume ramp** scales primary and prostate volume by media percent between a floor and ceiling. Curves: Linear, Exponential, Logarithmic, Smoothstep, Smootherstep. Distinct from motion rest-volume. Requires Timeline Absolute.

## Custom events

Optional **Custom events** load funscript-tools `.events.yml` files after the volume ramp. Supported axes: `volume`, `volume-prostate`, `pulse_frequency`, `pulse_width`, `frequency`, `alpha`, `beta`, and `e1`–`e4`. Three-phase defs (alpha/beta) and four-phase defs (e1–e4) require the matching ReStim mode.

Events evaluate at **send-time** absolute media position (`sample.due_at`, aligned with video / status), not the calculate-time clock used to build the look-ahead queue sample. Definitions are vendored in `vector1a/event_definitions.yml`.

Do not also bake the same events offline into authored funscripts (double apply).

## Architecture

No AI, voice, GPU, or cloud dependency. Timeline, ramp, and events are optional and off by default. Vector remains a standalone deterministic signal engine.

## Validation

137 automated tests pass, including timeline decode/freshness/hold, media ramp curves, event parse/eval/send-clock behaviour, plus the Alpha 49 suite.
