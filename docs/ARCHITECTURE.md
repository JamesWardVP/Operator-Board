# Architecture

```
operator_board/
  app.py                 entry point (UI or --headless)
  context.py             AppContext: builds every service and registers ALL operator actions
  qt.py                  PySide6 / PySide2 shim (only UI code imports Qt)
  core/
    config.py            AppConfig dataclasses (the whole stage + machine settings)
    stage.py             ConfigStore: where the stage lives (standalone, or Switchboard config + .opboard sidecar)
    switchboard_config.py  find Switchboard config folders; read, create and merge Switchboard configs
    serde.py             dataclass <-> dict (tolerant of unknown/missing keys)
    actions.py           ActionRegistry: id -> handler (+ params, toggle state)
    events.py            thread-safe pub/sub EventBus
    ue_versions.py       UE 5.2-5.8 table, Build.version detection
    switchboard_import.py  compatibility re-export of switchboard_config
  listener/              SwitchboardListener protocol: messages, TCP + QUIC clients, shared credential store
  devices/               launch command lines (editor / nDisplay / MU server) + DeviceManager
  multiuser/             MU server (local subprocess or via listener) + log-based client tracking
  led/                   transports (http/tcp/udp/osc), JSON protocol profiles, LEDProcessor, LEDManager
  remote/                Remote Control API client, targets, levels, cameras, objects, DLSS, takes
  sync/                  sync providers (FreeFileSync/P4/Git/Robocopy/custom) + manager
  analytics/             MetricStore (time series from listener sync status)
  surfaces/              surface layouts (JSON), SurfaceManager (several surfaces at once), controller, label fitting,
                         Stream Deck / MIDI / OSC backends, web panel, HTTP API for Companion
  ui/                    main window, theme, widgets, generic dataclass forms, one module per page
```

## Packaging
`packaging/build.py` runs PyInstaller with `packaging/operator_board.spec`, producing `dist/OperatorBoard/` with
`OperatorBoard.exe` (no console) and `OperatorBoardConsole.exe` (console) sharing one runtime, plus a zip.
With `--installer` it also runs Inno Setup 6 on `packaging/operator_board.iss`, giving
`OperatorBoard-Setup-<version>.exe` (per-user install by default, Start menu shortcut, uninstaller, closes a running
Operator Board on upgrade). The workflow `.github/workflows/build-windows.yml` tests and builds on every push and
attaches the installer and the zip to releases for `v*` tags. When frozen, logging also goes to `~/.operator_board/logs/operator_board.log`.

## Stage storage
| Mode | Files |
|---|---|
| Standalone | `~/.operator_board/config.json` holds everything. |
| Switchboard config | `<configs>/<Name>.json` (Switchboard owns it), `<configs>/<Name>.opboard` (Operator Board's per-stage settings), and `~/.operator_board/config.json` for machine settings (`surfaces`, `ui`, `switchboard`). |

- Switchboard lists configs with `rglob('*.json')` in its configs folder, so the sidecar must never end in `.json`.
- Switchboard's `Config.save()` rebuilds the file from its in-memory model, so it drops keys it does not know.
  Operator Board therefore never adds its own keys to the Switchboard file.
- On save the Switchboard file is re-read and only mapped keys are merged (`merge_into_switchboard`), so edits
  made in Switchboard meanwhile survive. Mapped: `project_name`, `uproject`, `engine_dir`, `current_level`,
  `muserver_server_name/session_name/slate_mode/clean_history/command_line_arguments`, and the `Unreal` /
  `nDisplay` devices (`address`, `autojoin_mu_server`, `port`, `fullscreen`, `settings.ndisplay_cfg_file`).
- Switchboard keeps the listener `port` per device type and drops per-device values when it saves; the
  sidecar keeps a non-default per-device port.
- Switching stage copies the new `AppConfig` into the existing object (`stage.copy_into`), because every service
  holds a reference to it, then calls `reload_all()`.

## SwitchboardListener connections
- **UE 5.2 - 5.3:** plain TCP, null-terminated JSON (`listener/client.py`).
- **UE 5.4+:** QUIC over UDP (ALPN `ue-switchboard`, `listener/quic.py`, using aioquic as Switchboard does). The listener's
  certificate is self-signed: trust on first use, SHA-256 fingerprint stored with the credential and checked
  afterwards (`FingerprintChanged`). The first message is `authenticate` (password, or a JWT on stores that are not
  encrypted at rest); the reply's `bAuthenticated` is the string `"true"`/`"false"`.
- **Message ids** come back from the listener as Unreal FGuid strings (uppercase, no dashes); always compare
  them with `protocol.same_id`.
- **Protocol choice** per device (`DeviceConfig.listener_protocol`): auto = QUIC first for 5.4+, TCP first before,
  falling back to the other on connection failure but never on an authentication error.
- **Credentials** (`listener/credentials.py`): Windows Credential Manager entries `Switchboard_<host>` (user name =
  fingerprint, secret = password), the same ones Switchboard's `CredentialStoreWindows` uses, so either program
  can use a password saved by the other. Elsewhere: JWTs in `~/.operator_board/listener_credentials.json`.
- `DeviceManager.connect()` publishes `device.auth` (password / rejected / fingerprint); `ui/listener_login.py`
  prompts once per machine and reconnects the other devices on it.

## Principles
- **One action registry.** Every operator operation is an action (`led.freeze`, `group.start`, `camera.recall`, …). The UI buttons, Stream Deck keys, Companion HTTP calls and hotkeys all call `ctx.actions.invoke(id, **params)`. A new feature becomes available on every surface by registering it once in `context.py`.
- **Core has no Qt dependency.** Services publish events on the `EventBus`. `ui/bridge.py` moves those events onto the GUI thread. Network calls from the UI run through `run_async` so the UI never blocks.
- **Data-driven integrations.** LED protocols (`led/profiles/*.json`), DLSS controls (`remote/dlss.json`) and surface layouts are JSON, so changes in a vendor or plugin API need no code changes.
- **Version tolerance.** Config loading ignores unknown keys. Python snippets run inside Unreal (`remote/scripts.py`) try several API spellings to cover UE 5.2–5.8. The code is kept Python 3.9 compatible because UE 5.2's Switchboard ships Python 3.9.

## Talking to Unreal
| Channel | Used for |
|---|---|
| SwitchboardListener 2980 (QUIC/UDP for UE 5.4+, TCP for 5.2-5.3) | start/kill processes, stdout, `get sync status` metrics, sync jobs on nodes |
| Remote Control HTTP 30010 | console commands, property get/set, function calls, presets, asset search |
| Remote Control → `PythonScriptLibrary.ExecutePythonCommandEx` | editor-only operations; results are returned by printing `OPB_RESULT:<json>` |
