# Vector 1A 1.6.0-alpha53

Alpha 53 syncs funscript-tools’ MCB extract composite into Vector’s live event expander so `mcb_extract` / `mcb_extract_4p` match offline Apply.

This build is published on the Senorgif33 fork of Vector 1A. Upstream Alpha 52 behaviour is unchanged when these extract events are unused.

## MCB extract composite

Bundled definitions replace the old static extract recipes with the Relentless→overload composite:

- YAML shell: pulse width 65, duration-scaled pulse Hz segments and amp ladder (default ~265 s; minimum 60 s)
- Hub–spoke motion baked at expand time via `vector1a/extract_composite.py`
  - `mcb_extract`: N hub ↔ L/R spokes on primary `alpha` / `beta`
  - `mcb_extract_4p`: E1 hub ↔ E2/E3/E4 one-hot spokes
- Optional `seed` param for reproducible spoke order (omit for random each expand / `EVT`)

Removed (to match funscript-tools): `mcb_extract_additive`, `mcb_extract_4p_additive`. Files naming those events will fail to load until renamed.

## Sensor suppression (S1) on new MCB events

- `mcb_extract` / `mcb_extract_4p`: 50% mute for full event duration (edge policy)
- `mcb_orgasm_countdown`: full mute (100%) from orgasm cue only (+21 s / `$orgasm_offset_ms` through climax)
- `mcb_orgasm_countdown_stroke_override`: 50% during countdown lead-in, then 100% from +21 s
- `mcb_goodboy`: no S1 steps (sensors stay at baseline)

## Compatibility

- Orgasm/goodBoy events from Alpha 52 are unchanged.
- Do not also bake the same events offline into authored funscripts (double apply).

## Validation

Automated tests cover extract derive/scale/min-duration, seeded 3P/4P motion axes, expand shell+motion merge, seed determinism, and S1 timing on orgasm/extract events, plus the prior Alpha 52 suite.
