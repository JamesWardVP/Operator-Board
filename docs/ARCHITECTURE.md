# Architecture

```
operator_board/
  app.py                 entry point (UI or --headless)
  context.py             AppContext: builds every service and registers ALL operator actions
  qt.py                  PySide6 / PySide2 shim (only UI code imports Qt)
  core/
    config.py            AppConfig dataclasses -> one JSON file (~/.operator_board/config.json)
    serde.py             dataclass <-> dict (tolerant of unknown/missing keys)
    actions.py           ActionRegistry: id -> handler (+ params, toggle state)
    events.py            thread-safe pub/sub EventBus
    ue_versions.py       UE 5.2-5.8 table, Build.version detection
    switchboard_import.py  import devices from a Switchboard config
  listener/              SwitchboardListener protocol (null-terminated JSON over TCP 2980) + client
  devices/               launch command lines (editor / nDisplay / MU server) + DeviceManager
  multiuser/             MU server (local subprocess or via listener) + log-based client tracking
  led/                   transports (http/tcp/udp/osc), JSON protocol profiles, LEDProcessor, LEDManager
  remote/                Remote Control API client, targets, levels, cameras, objects, DLSS, takes
  sync/                  sync providers (FreeFileSync/P4/Git/Robocopy/custom) + manager
  analytics/             MetricStore (time series from listener sync status)
  surfaces/              surface layouts (JSON), controller, Stream Deck backend, HTTP API for Companion
  ui/                    main window, theme, widgets, generic dataclass forms, one module per page
```

## Principles
- **One action registry.** Every operator operation is an action (`led.freeze`, `group.start`, `camera.recall`, …). The UI buttons, Stream Deck keys, Companion HTTP calls and hotkeys all call `ctx.actions.invoke(id, **params)`. A new feature becomes available on every surface by registering it once in `context.py`.
- **Core has no Qt dependency.** Services publish events on the `EventBus`. `ui/bridge.py` moves those events onto the GUI thread. Network calls from the UI run through `run_async` so the UI never blocks.
- **Data-driven integrations.** LED protocols (`led/profiles/*.json`), DLSS controls (`remote/dlss.json`) and surface layouts are JSON, so changes in a vendor or plugin API need no code changes.
- **Version tolerance.** Config loading ignores unknown keys. Python snippets run inside Unreal (`remote/scripts.py`) try several API spellings to cover UE 5.2–5.8. The code is kept Python 3.9 compatible because UE 5.2's Switchboard ships Python 3.9.

## Talking to Unreal
| Channel | Used for |
|---|---|
| SwitchboardListener TCP 2980 | start/kill processes, stdout, `get sync status` metrics, sync jobs on nodes |
| Remote Control HTTP 30010 | console commands, property get/set, function calls, presets, asset search |
| Remote Control → `PythonScriptLibrary.ExecutePythonCommandEx` | editor-only operations; results are returned by printing `OPB_RESULT:<json>` |
