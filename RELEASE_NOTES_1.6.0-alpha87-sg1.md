# Vector 1A 1.6.0-alpha87-sg1

Senorgif fork build on upstream **`release/1.6.0-alpha87`** @ **`faf8e1f`**.

Versioning uses `1.6.0-alpha{UP}-sg{K}` so fork releases never collide with Nephet2 `alphaN` numbers. This is **sg1** on upstream floor **87**. Fork-only fixes on the same floor become **sg2**; the next upstream adapt resets to **alpha88-sg1** (or later).

Commission with ReStim's graphical display and stimulation hardware disconnected. Vector 1A is not a medical device.

## Upstream base (included)

Everything from upstream alpha79–alpha87 remains, including:

- Howl / Civet / Custom 4-phase spatial curves and profiles
- Secondary carrier on `C0` for both outputs (alpha84)
- Response Min/Max handles, scrollable UI, volume-match idle floor (alpha85)
- Six live parameter-response curves + profiles + 3D composite (alpha86)
- Fixed Start/Neutral/Stop chrome, connection diagnostics, output snapshots/composer, semantic tempo `0.5x`/`1x`/`2x` via Director (alpha87)
- Bundled Bleak / Pillow Windows source story

See [Alpha79](RELEASE_NOTES_1.6.0-alpha79.md)–[Alpha87](RELEASE_NOTES_1.6.0-alpha87.md) for upstream detail.

## Fork delta

- Independent prostate stroke axis (default `L3`); no silence fallback to `L0` when MFP axis is selected
- LAN Remote API (`control_api`) beside Director (no semantic-tempo Remote API; tempo stays Director-only)
- Media timeline ramp waypoints / funscript I/O and PATH sidecar auto-load helpers
- Send-capture logging
- Director event allowlist YAML policy (curated exclude list)
- Extract recipes folded to **`mcb_extract_dual`** and **`mcb_extract_beat_dual`** (3P + 4P baked together at expand); beat min duration 25s
- EVT/PATH MFP dispatch helpers
- Windows **exe** packaging path retained (`build-exe.bat`) alongside upstream source ZIP

## Known gaps on this land

- Restim I-suffix UI setting is saved but not applied on the wire yet (`network.py` send helpers do not take `interval_ms` / `s1`)
- Dark mode does not restyle upstream `RangeBar` widgets
- Exact-output upstream fixtures that assume stock event framing still need fork-aware updates

## Downloads / upgrade

Extract the Windows ZIP (source or exe) and run the launcher for this version string. Copy `profile/Vector1A/settings.json` from a previous folder to keep ports and preferences. Event companions that still call `mcb_extract` / `mcb_extract_4p` (or beat siblings) must switch to the dual names.

## Validation

Run `python -m pytest -q` on this branch before tagging. No physical stimulation hardware is used for automated validation.
