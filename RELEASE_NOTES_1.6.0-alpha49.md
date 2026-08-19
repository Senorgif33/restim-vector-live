# Vector 1A 1.6.0-alpha49

Alpha 48 polishes the authored-axis router and turns Session startup into a coordinated standalone workflow.

## Live axis ownership

The MFP authored-axis window now marks each axis with its current owner:

- **AUTHORED** — the live MFP value is being sent to ReStim.
- **VECTOR** — Vector is generating that ReStim axis.
- **MFP (not routed)** — MFP is supplying the axis, but the current manual policy is not forwarding it.
- **inactive** — the axis was detected previously but is not currently live.

This complements the existing `Detected this session`, `Currently live`, and routing-mode readouts, making hybrid authored/generated sessions easier to diagnose.

## Coordinated Session startup

The Session startup window can optionally manage MultiFunPlayer, Primary ReStim and Prostate ReStim.

When enabled, Vector now:

1. prepares its MFP listener;
2. launches selected external applications;
3. detects already-running ReStim services rather than opening duplicate instances;
4. waits up to 12 seconds for each newly launched ReStim service to accept connections;
5. performs the ReStim WebSocket connection only after the service is ready; and
6. reports a single top-level session state: **STARTING**, **READY**, or **ATTENTION**.

Startup waiting runs off the Tk UI thread, so Vector remains responsive while external programs initialise.

If a ReStim connection is lost after a coordinated READY state, Vector changes the session state to ATTENTION while the existing ReStim reconnect logic continues to operate.

All startup options remain disabled by default. Vector continues to work exactly as a manually started standalone application unless the user explicitly enables them.

## Architecture

No AI, voice, Ollama, SillyTavern, F5-TTS, GPU, or cloud dependency has been added. Vector remains standalone. Any future Director/voice integration is intended to remain optional and external to the deterministic signal engine.

## Validation

111 automated tests pass in the Alpha 48 source tree, including authored-axis routing, historical-delay behaviour, WebSocket output, orchestration port readiness, settings, four-phase transforms and Rolling Variety.


## Alpha49 startup fix

- Windows `.exe` launch targets now start with their own containing folder as the process working directory.
- This is especially important for multiple ReStim copies, where each folder can carry its own configuration/INI and network port.
- `.bat`/`.cmd` targets also run from their containing folder; `.lnk` targets continue to use Windows ShellExecute behavior.
- No signal-generation or routing mathematics changed.
