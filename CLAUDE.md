# CLAUDE.md: project context for Claude Code

The project brief, workflow and the decisions made so far, so any session (local or cloud) can continue without re-explaining. Update it when significant decisions are made.

## The brief (from the project owner)
Operator Board is a Virtual Production, operator-friendly extension to Unreal Engine **Switchboard**: an easily navigable UI for controlling UE virtual production.

1. **Primary:** one-button **freeze** / **blackout** of the LED wall, or a **test pattern** from the LED processor. The user picks their processor type; if it is networked and configured, commands go straight to it. Support all major processors, connecting **the way Bitfocus Companion does** (host/IP + port per module).
2. Standard Switchboard functions on easy-to-read displays:
   - connect to and start UE instances (editor, nDisplay nodes…);
   - start, name and monitor a **multi-user server** (show the connected count);
   - start and stop **groups** of instances.
3. **File sync / revision** (FreeFileSync etc.) and **analytics** for live instances (raw data and graphs), on **separate pages** from the main display.
4. Load and swap **levels** within a project, and if possible between **projects**.
5. A list of **instance-editable objects** controllable remotely.
6. **Control surface (Stream Deck)** configuration:
   - covers every standard operation (levels, multi-user, freeze, blackout…);
   - has a **default config** and user customisation;
   - **saves and loads an external file** for use on another machine.
7. An easy UI for creating, controlling and saving **takes**, as in Switchboard.
8. **DLSS** settings control when the project uses the DLSS plugin.
9. Toggle **sub-levels** (often lighting setups) and move between **saved camera locations**.
10. Future ideas live in `docs/ROADMAP.md`, never the README. The **README lists only shipped features and the supported UE versions (5.2 → 5.8)**.
11. GitHub: `JamesWardVP/Operator-Board`.

**Next priorities, chosen by the owner and not started:** Custom Pages and custom pictures on control surface buttons. They are specified in `../FUTURE_UPDATES.md`, beside the repo rather than in it. Don't start them until the owner says so.

## Current state (v0.8.0)
Every brief item has a first implementation. See:
- README.md for features;
- docs/ARCHITECTURE.md for structure;
- docs/LED_PROCESSORS.md for protocol research;
- docs/ROADMAP.md for ideas.

**Basics:**
- **Stack:** Python + Qt (PySide6, PySide2 fallback via `operator_board/qt.py`), so it also runs in Switchboard's own Python venv.
- **Run:** `python -m operator_board` (`--headless`, `--no-hardware`, `--config path`, `--switchboard-config path`, `--project`).
- **Tests:** `python -m pytest`. The suite has:
  - fake SwitchboardListener and HTTP servers;
  - an offscreen Qt smoke test (`QT_QPA_PLATFORM=offscreen` on headless Linux).
- **Example stage:** `config/example_stage.json` (simulated LED processors).
- **README screenshots:** `python docs/make_screenshots.py` renders `docs/images/*.png` from a simulated demo stage. It needs a desktop session for fonts.

**Logo:** `packaging/logo.svg`, the owner's design, cyan #00d5ff. Re-run `python packaging/make_icon.py` (needs Pillow) after changing the SVG. It renders:
- `operator_board/ui/icon.png` and `icon_small.png`;
- `operator_board/ui/logo.png` (sidebar, cyan);
- `docs/images/logo.png` (README, cyan);
- `packaging/operator_board.ico` (16-256 px; app icons are the black logo, as large as fits, on a white rounded square);
- the installer wizard pictures `packaging/wizard_image_*.bmp` and `wizard_small_*.bmp` (black on white, 100/150/200 %).

**Licence and signing:**
- MIT (`LICENSE`); bundled components are listed in `THIRD_PARTY_NOTICES.md`. Add nothing proprietary, because SignPath requires everything to be open source.
- Releases are unsigned, so SmartScreen warns once for the setup file; the installed app starts without warnings.
- **SignPath Foundation** (free for open source) declined for now, as the project isn't popular enough yet. Re-apply later. Its conditions are why the README has "Code signing policy", "Privacy policy" and uninstall steps, and why every release links to them.
- When SignPath approves: sign both exes in `.github/workflows/build-windows.yml` before zipping, with credentials in GitHub Secrets.
- The other free no-warning route is the Microsoft Store (free developer account; Microsoft signs the MSIX).

**Taskbar:** `SetCurrentProcessExplicitAppUserModelID("JamesWardVP.OperatorBoard")`. The installer shortcuts use the same ID.

## Workflow: building, releasing, website (keep to this)
**Branches:**
- Work on `Development`, then open a PR to `main`.
- **The owner merges**, unless they ask me to: `gh pr merge <n> --merge` works when asked.
- I tag after the merge.

**Local build:**
1. Close any running Operator Board first (`Get-Process OperatorBoard*`: `CloseMainWindow()`, then `Stop-Process`). Windows locks `dist/`.
2. Run `.venv/Scripts/python.exe packaging/build.py --hidapi`. The system Python has no PyInstaller.

The build:
- rebuilds `dist/OperatorBoard/` (`OperatorBoard.exe` + `OperatorBoardConsole.exe`);
- writes `dist/OperatorBoard-<version>-win64.zip`;
- writes `dist/OperatorBoard-Setup-<version>.exe` when Inno Setup 6 is installed (`--installer` requires it, `--no-installer` skips it).

Older packages are removed. Always build into `dist/`, never side folders.

**Installer:** `packaging/operator_board.iss`:
- per-user install by default;
- closes a running Operator Board on upgrade;
- **never change its `AppId`**.

**Release:**
1. `python packaging/bump_version.py X.Y.Z`. It updates `operator_board/__init__.py`, the README and the `## Current state (vX.Y.Z)` heading above, so keep that heading's wording.
2. Run the tests and check the result: a piped test command once hid failures.
3. Commit and push `Development`, then open a PR to `main`. The owner merges.
4. Tag `vX.Y.Z` on `main` and push the tag.
5. CI (`.github/workflows/build-windows.yml`) builds on every push. On a `v*` tag it attaches the installer and zip to the release. It fails if the tag doesn't match the version.
6. Add "What's new" notes **above** the automatic install text: `gh release edit vX.Y.Z --notes-file` with the existing body appended.

**Website (virtualfilmtools.com):**
- `.github/workflows/publish-website.yml` uploads the installer, zip, `releases.json` and `latest.json` to `https://virtualfilmtools.com/downloads/operator-board/` over FTPS.
- The two JSON files are built by `packaging/website/release_data.py` from the GitHub releases. Files go up first, then `releases.json`, then `latest.json`.
- It runs automatically from the Windows build for **X.Y.0** tags, or manually: `gh workflow run publish-website.yml --ref main -f tag=vX.Y.Z`.
- Notes added after the publish are not on the site until it is re-run, so re-run it after step 6.
- FTP certificate: the server shows Hostinger's `*.hstgr.io` certificate, so lftp trusts exactly that certificate by SHA-1 fingerprint. It's the repository variable `VFT_FTP_CERT_FINGERPRINT`, with a default built in. When the certificate is renewed, the run prints the new one.
- The page itself (`../../VirtualFilmTools`, `https://virtualfilmtools.com/operator-board/`) belongs to a separate "Virtual Film Tools website hub" session.
- After a release that changes features or UI, brief that session: what changed, which tag to render screenshots from, and the text to remove or add. It renders its own screenshots and goes live with the owner's approval.

## Key decisions & research findings
**Architecture**
- **Single ActionRegistry** (`context.py`): the UI, Stream Deck, Companion HTTP API (`/api/action/<id>`) and hotkeys all invoke the same actions. Add new features as actions.
- **Code layout:** core packages must not import Qt; only `ui/` and `qt.py` do. `editor.*` events go to every page, not only the visible one.

**Switchboard listener** (mirrors Switchboard's `message_protocol.py`)
- **Messages:**
  - null-terminated JSON;
  - commands `start`, `kill`, `get sync status`; the keepalive command is `"keep alive"`;
  - program name `"unreal"`.
- **Transport:**
  - **UE 5.4+ uses QUIC over UDP 2980 with a password**; TCP 2980 is closed.
  - 5.2-5.3 use TCP.
  - Checked against Switchboard 5.2/5.6/5.8 sources and a real 5.8 listener: handshake, `authenticate` reply, wrong-password rejection.
- **Replies and program messages:**
  - replies echo ids as FGuid strings, so compare them with `protocol.same_id`;
  - program messages nest details under `process` (`uuid`, `pid`, `name`, `caller`);
  - every connection hears every program, so `caller` must be the device name, and messages are filtered by it (`protocol.program_info`).
- **nDisplay nodes:** the cluster JSON is extracted from the config asset and written to the node with `send file` (`devices/ndisplay.py`).
- **Passwords:** shared with Switchboard through Windows Credential Manager (`Switchboard_<host>`). Verified by reading an Operator Board entry with Switchboard's own code.
- **Local listener version** (`listener/local.py`):
  - finds running SwitchboardListener processes (tasklist + QueryFullProcessImageNameW) and their engine from the exe path;
  - `ctx.check_local_listener()` compares that engine with the open config's; the same folder or the same major.minor counts as OK;
  - `MainWindow._check_listener` runs 1.5 s after opening and on stage/project changes, and offers `ctx.replace_local_listener()`: disconnect local devices, taskkill, start the right listener, reconnect.
- **Unreachable listener:**
  - an OSError on connect publishes `device.listener_unreachable`, with `local` set for this PC;
  - `MainWindow._offer_listener` asks once, then not again for 2 minutes after an answer;
  - `AppContext.start_local_listener` starts `<engine>/Binaries/Win64/SwitchboardListener.exe` detached unless one is running;
  - `reconnect_local_devices` then retries for 20 s.

**Switchboard configs** (`core/switchboard_config.py`, `core/stage.py`; the Stage page is titled "Switchboard Configs")
- Opens, creates and saves the real config files in Switchboard's own folder: `<Engine>/Plugins/VirtualProduction/Switchboard/Source/Switchboard/configs`, or `SWITCHBOARD_CONFIG_DIR` from 5.6.
- Format checked against the 5.2/5.6/5.8 sources and real 5.8 configs.
- Operator Board-only settings go in a `<name>.opboard` sidecar, never `.json`, because Switchboard rglobs `*.json`.
- Only mapped keys are merged, so real configs round-trip unchanged.
- Switchboard 5.8's own `Config` + `DeviceManager` loaded an Operator Board config. Switchboard drops the per-device listener `port` on save (it is per device type), so the sidecar keeps it.

**Remote Control and Python in Unreal**
- **Remote Control** is used for everything inside Unreal. Editor-only operations run Python through `ExecutePythonCommandEx` and return JSON via an `OPB_RESULT:` print marker; they need the Python Editor Script Plugin.
  - `run_python` raises on a `{'error': ...}` result.
  - `scripts._lit` writes Python literals; json.dumps gave `true`, which broke the bool light toggles.
- **Security switches** (`URemoteControlSettings`, all off by default):
  - `bEnableRemotePythonExecution` (5.2+);
  - `bAllowConsoleCommandRemoteExecution` (5.6+);
  - `bAllowAnyRemoteFunctionCall` (5.8 allowlist).
  - Without them, Take Recorder, levels and cameras fail with "Object Default__PythonScriptLibrary cannot be accessed remotely".
  - At launch Operator Board passes `-ini:RemoteControl:[/Script/RemoteControlCommon.RemoteControlSettings]:...=True` (`launcher.REMOTE_CONTROL_UNLOCK`, setting `remote_control.allow_remote_features`). Verified on 5.8: blocked without, allowed with.
  - 5.8 also blocks Remote Control calls such as K2_GetActorLocation without the unlock, so cameras use Python.
- **Ports** (verified on 5.8):
  - UE ignores `-RCWebControlPort`. The HTTP port is the `RemoteControlHttpServerPort` setting (default 30010); the WebSocket port is `RemoteControlWebSocketServerPort`, which the launcher sets to the HTTP port + 10.
  - The HTTP server listens on 127.0.0.1 unless `-ini:Engine:[HTTPServer.Listeners]:DefaultBindAddress=0.0.0.0` is passed. Operator Board adds it for devices not on this PC.
  - `devices.manager.unique_rc_ports` gives every device on one PC its own port on reload (event `device.rc_port_changed`). Before that, an editor and a node on one PC both used 30010: editor requests reached the node ("'NoneType' object has no attribute 'get_all_level_actors'"), and the editor-ready check never passed.
- **Editor ready** (`remote/editor_watch.py`):
  - every 3 s (5 s once ready), a short-timeout Python check: the editor world exists and `AssetRegistry.is_loading_assets()` is false;
  - publishes `editor.ready` / `editor.level_changed` / `editor.lost`;
  - Cameras & Levels and Remote Objects then pull their lists quietly (`BasePage.background(quiet=True)`) and show "No ... found" placeholders.
- **No editor running:**
  - `TargetResolver.editor_or_nodes(fn, all_nodes)` runs on the primary editor;
  - only when the editor isn't set up or is unreachable, it runs on the first nDisplay node that answers, or on every node for changes;
  - `ObjectService` reads and quick-control changes use it (verified on a real -game node: actors, quick controls, describe, values, Set);
  - quick-control changes skip the editor transaction in a game world.
- **-game nodes** run Python too, with no editor subsystem. `scripts.LEVEL_ACTORS` uses the editor's actors, else the game world with the most actors.
- **Actor property values:** one `/remote/object/property` READ_ACCESS without a property name returns them all.
- **DLSS:** data-driven in `remote/dlss.json` (DLSSLibrary.SetDLSSMode, StreamlineLibraryDLSSG.SetDLSSGMode, plus cvars).

**Cameras, lights, presets**
- **Cameras:**
  - listed by class in Python (`CameraActor`, `DisplayClusterRootActor`, so Blueprint nDisplay configs appear);
  - capture/recall in the editor use `get_actor_transform`/`set_actor_transform` in a ScopedEditorTransaction; without an editor, they run on every nDisplay node;
  - duplicate names get `_2`, `_3`.
- **Light/camera quick controls** (`scripts.actor_quick_controls` / `set_actor_quick_control`; verified on real RectLights and a CineCameraActor in 5.8):
  - light colour is sRGB from the picker, converted to linear before `set_light_color(..., True)`;
  - values are locked when the actor has a LiveLinkComponentController, a LensComponent or a DMX component.
- **Light units:** `set_intensity_units` doesn't convert, so `set_actor_quick_control` mirrors the engine's ComputeLightBrightness/SetLightBrightness for point, spot and rect lights in Nits and EV. Verified on a rect light: 3 cd = 9.42 lm = EV 4.58.
- **Saved settings presets** (`core/presets.py`, `AppConfig.setting_presets`):
  - items of ITEM_TYPES are applied in ITEM_ORDER by `steps()`;
  - each step raises `PresetProblem` on missing data, a failure or a failed check, and the Saved Settings page asks retry / skip / cancel;
  - the level step waits until the editor has the level open.
- **Remote Objects Undo** re-applies a record's previous value through `ctx.apply_setting(kind, ref, value)` (kind quick / property / preset). `ctx.changed_settings()` is the latest value of each setting changed this run.

**Takes** (Take Recorder, 5.8)
- **A just-opened panel** has no take metadata until the editor ticks, so `take_recorder_start` returns `retry` and `TakeManager` calls again. Verified up to `start_recording`, but not that call itself.
- **Sources:** it records only the sources in its panel; with none, `start_recording` raises "There are no currently enabled or valid sources", which Operator Board explains.
  - The optional `takes.take_preset` asset is applied first with `setup_for_recording_take_preset`.
  - For an empty take, `takes.auto_add_camera` adds a source with `TakeRecorderActorSource.add_source_for_actor`. It picks, in order: a `take_camera` label; the ICVFX camera's `camera_settings.external_camera_actor`; the nDisplay root holding an ICVFX camera; a CineCameraActor; the nDisplay root. On the real nDisplayTest level it picked CineCameraActor1.
- **Panel updates:** slate/take changes reach an open panel through one background sender that always sends the latest values.
- **Countdown:**
  - `take_recorder_start` returns `TakeRecorderUserSettings.settings.countdown_seconds` (default 3);
  - `TakeManager.phase` stays "countdown" until `take_recorder_status` reports `TakeRecorderState.STARTED` (from `get_active_recorder().get_state()`; `get_countdown_seconds()` is the live remainder);
  - stopping during the countdown calls `cancel_recording`, and nothing is logged.
- **Save:**
  - after stop, `panel.get_last_recorded_level_sequence()` (remembered as `TakeRecord.sequence`);
  - `EditorAssetLibrary.save_asset(..., only_if_is_dirty=True)` for the sequence and its `_Subscenes`; a clean asset is not rewritten;
  - in a session, `unreal.MultiUserClientStatics.persist_specified_packages` (local disk only, nothing broadcast);
  - when the panel forgot the take, Save looks it up by `<slate>_<take>` under Take Recorder's save folder, with a 180 s timeout.
- **Multi-user long paths:**
  - the Concert sandbox copy of a recorded subsequence (`Intermediate/Concert/MultiUser/<id>/<id>/Sandbox/Sandbox/<Project>/Content/<take path>`) failed at 320 characters with Windows long paths off ("ConcertSyncSessionDatabase: Unable to open a file for writing");
  - `take_recorder_start` estimates that length in the editor (exact for the reported case), and Record warns;
  - a failed take stays in the session, so every client that joins later shows the same error until long paths are on or the session is cleaned.
- **Timecode/genlock** (`remote/timing.py`):
  - `TimingMonitor` runs `scripts.editor_timing` on the primary editor every second (`unreal.TimeManagementLibrary.get_timecode()`, about 20 ms);
  - zeros or no reply mean "No data"; the UI ticks the timecode on between polls;
  - the engine's timecode provider and the custom time step's lock state aren't reachable from Python in 5.8, so genlock comes from the listener sync status `syncTopos` (`SYNC_TOPOS` flag; every `syncGpus[].bIsSynced`, as Switchboard's nDisplay monitor reads it);
  - failing that, the editor's custom time step name is shown as a setting, not as proof.

**Multi-user** (all verified on a real 5.8 server)
- **Session flags:** the server may only get `-CONCERTSESSION` together with `-CONCERTPROJECT=<uproject name>`. Alone, it creates a project-less session that refuses every client. With both, editors join, and an existing session of that name is reused. Sessions are recovered after a forced shutdown.
- **Flow:**
  - `start(interactive=True)`, the UI button, starts the server without a session;
  - on a name clash it sets `clash` and publishes `multiuser.session_clash`, and the Operate page asks: new session or keep; `resolve_clash` handles the answer;
  - `start_session()` restarts with `-CONCERTSESSION/-CONCERTPROJECT` (no `-CONCERTCLEAN`);
  - `auto_start_session` (default off) does that straight after start when there's no clash;
  - `session_live` comes from start_session, or from a client creating/joining the session after the server is ready. A session recovered at start-up doesn't count.
- **Button colours:** purple START SERVER, orange START SESSION, pale green STOP SERVER (live).
- **Numbering** (`multiuser.auto_increment_session`, default on):
  - `MultiUserServer.stop()` moves to `next_session_name` (VP_Session -> VP_Session_2, keeping zero padding);
  - `start()` waits up to 20 s for "Multi-User Editing Server Initialized", collecting the "Live session 'X' (id) was recovered." lines printed before it;
  - if the name is among them, it moves to the next free name with a warning (event `multiuser.session_renamed`) and saves the config;
  - devices build their command line after the auto-start hook, so they join the new name.
- **Archive last session** (`multiuser.archive_last_session`):
  - Concert's `UConcertServerConfig.bAutoArchiveOnReboot` (`-ini:Engine:[/Script/ConcertServer.ConcertServerConfig]:bAutoArchiveOnReboot=True`, `launcher.ARCHIVE_ON_START`) archives live sessions left by a forced stop as the server starts ("Live session 'X' (id) was archived on reboot.");
  - `MultiUserServer._archive_previous` restarts the server with it once a new session name is settled and older sessions exist: after a start without a clash, or after "new session" at the clash prompt;
  - keeping the existing session archives nothing.
- **Client count:** parsed from server logs with configurable regexes. The Concert wording is unverified.

**Projects** (`core/projects.py`)
- **Storage:** one folder per project in `~/.operator_board/projects/<id>/`:
  - `project.json`: name and details;
  - `config.json`: the project's stage settings and its `switchboard.active_config`;
  - `stages/<config>.opboard`: settings per Switchboard config;
  - `take_log.json` and `change_log.jsonl`.
- **ConfigStore** (`ConfigStore(global_path, project_dir)`):
  - machine fields come from the global config, but the project keeps its own active Switchboard config;
  - `stage_path` and `sidecar_for` point into the project.
- **AppContext** (`AppContext(project_dir=...)`): passes the folder to TakeManager (`log_dir`) and ChangeLog, and reads `ctx.project` (ProjectInfo, used in the production export Summary).
- **Start-up** (`app.py`):
  - shows `ui/start_screen.choose_project` first (headless: `--project` or `ui.last_project`);
  - runs with `setQuitOnLastWindowClosed(False)`, and returns to the start screen when `MainWindow.switch_project()` closes the window;
  - `migrate_existing_setup()` turns pre-project settings into "My stage" once.
- **Export/import:**
  - export is a `.opproject` zip; templates leave out SHOOT_FILES;
  - import always adds a new project and refuses unsafe zip paths;
  - `create(setup_from=...)` / `clear_shoot_data` drop cameras, presets and favourites, and reset the take.

**Change log and exports** (`core/changelog.py`)
- **What's logged:** `ctx.changes` records every `action.invoked` (with params, label, category) except `AppContext.NOT_LOGGED`: surface pages, LED wall/group states (`led.*` apart from `led.brightness`), `devices.*` and `group.*`.
  - It also records Remote Objects, sub-level and preset changes, with slate, take, timecode, project, level, value and previous value.
  - It writes to `~/.operator_board/change_log.jsonl`.
- **Exports:**
  - CSV;
  - `ctx.production_export`: an .xlsx (openpyxl) with Summary, Change log, Takes, Camera positions, LED processors and Unreal instances sheets.

**Control surfaces** (`surfaces/`)
- **Surfaces:**
  - several at once: `surfaces/manager.py` SurfaceManager, one SurfaceController per surface, `ctx.surface` = the first;
  - each SurfaceLayout has `id`, `name` (unique), `enabled` and `device` (Stream Deck serial or MIDI input name; empty = the next free matching device);
  - stored as dicts in `surfaces.devices`, a machine setting, so the same on every stage;
  - the old `layout_path` / `midi_*` settings become the first surface once (`devices_migrated`).
- **Export file:** `{"format": "operator-board-surfaces", "surfaces": [...]}`. An old single-layout file also imports.
- **Stream Decks:**
  - `StreamDeckBackend.scan()` opens new decks and drops unplugged ones; `assign()` matches the serial first, then the key count;
  - `MainWindow.nativeEvent` catches WM_DEVICECHANGE and calls `ctx.refresh_surface_devices(only_if_changed=True)` after 1.5 s; MIDI is reopened only when the input list changed.
- **Models:** `layout.MODELS` lists Stream Decks, web panel sizes, OSC, MIDI (APC mini, Launchpad programmer mode, 16-pad) and Custom.
  - `MIDI_MAPS` number the pads from the bottom-left.
  - MIDI is untested on real hardware.
- **New surfaces:**
  - get the default buttons re-flowed per page (`layout.fit_to_model` / `reflow`);
  - when there's more than one page, the last key of every page is Next page;
  - `ensure_page_navigation` runs on new pages and size changes only, so an operator can clear it.
- **Key text:**
  - `text_color`;
  - `font_size` (px on a 72-px key, scaled; 0 = auto);
  - `font_weight`;
  - `word_wrap`.

  `surfaces/render.fit_label` is shared by the Stream Deck (Pillow, Segoe UI by weight) and the on-screen deck, and shrinks the text before breaking a word.
- **Web panel** (`/panel` on the HTTP API, `surfaces/web_panel.py`):
  - polls `/api/surface` and presses `/api/surface/press/<n>`;
  - "Allow phones and tablets" binds 0.0.0.0 and creates a token;
  - `?surface=<id>` picks a surface, otherwise the first web surface (else Custom).
- **OSC** (`surfaces/osc.py`): pure-Python UDP, `/operator/...`. `/operator/surface/<n|name|id>/...` picks a surface, otherwise the first OSC surface (else Custom).
- **MIDI** (`surfaces/midi.py`): optional mido + python-rtmidi, bundled when installed.
- **Button actions:**
  - `osc.send` (`surfaces/osc_send.py`, Companion-style argument types);
  - `macro.run` (`AppContext.run_steps`: steps are `{"action", "params"}` or `{"delay": ms}`; failures are reported and the rest still run; no nesting).

  Their editors are in `ui/action_editors.py`.

**LED processors**
- **Modes:**
  - each processor has one `mode` (`LEDProcessor.mode`): normal, frozen, blackout or pattern; blackout outranks pattern, which outranks frozen;
  - `LEDManager.set_mode(target, mode, pattern)` (action `led.set_mode`) takes a processor, an LED group (`AppConfig.led_groups`, `LEDGroupConfig`) or "all";
  - it clears the other modes first, and returns to normal when they're all already in that mode;
  - unknown modes are rejected, such as an old "locate".
- **Locate was removed in 0.8:** operators wanted test patterns and block colours per processor, not the vendors' identify functions.
- **Test patterns:** `led.test_pattern` / `led.test_pattern_off` take a `target` (processor, group or "all"). Operate has an "on" choice.
- **UI:**
  - `ui/led_controls.ModeButtons`: the four lit buttons; a row on LED Processor Setup and in its Advanced panel, a column on the LED Processors page;
  - `ui/led_advanced.LEDAdvancedButton`: the "Advanced: groups and individual control" drop-down on LED Processor Setup and on Operate, a ModeButtons row with its own pattern choice per group and processor;
  - unsupported buttons are greyed out under faded tape (`widgets.set_unsupported`, `led_controls.unsupported_reason`);
  - `ui/led_status.status_groups` groups processors by state for `WallStatusBar.show_processors`: one "ALL PROCESSORS ..." bar, or one part per state.
- **Wall check:**
  - `AppContext._led_monitor` runs at start-up and every 15 s while any processor isn't confirmed online;
  - Operate overlays the LED card with hazard tape (`widgets.HazardOverlay`) when no processor is set up or all are unreachable.
- **Profiles:**
  - built-ins are never edited;
  - `ProfileRegistry.save_custom` writes `<id>_custom.json` (`based_on`, name "(custom)") to `~/.operator_board/led_profiles`; edited commands get confidence "user";
  - editing a custom type saves in place; `delete_custom` returns `based_on`;
  - profile `lookups` fill a `{key}` placeholder from the processor itself (COEX `{screen_ids}` from GET /api/v1/screen) or from the processor option of that name.
- **Protocols:** researched against the Bitfocus Companion modules installed on the owner's PC (`%APPDATA%\companion\modules`: brompton-tessera 2.2, mvr-helios 2.3, novastar-controller 2.6, novastar-coex 2.0). None of them has an identify/locate command.
  - **NovaStar:**
    - binary TCP 5200 (VX Pro: 15200); checksum = sum(bytes[2:]) + 0x5555, LE uint16; the module rewrites byte 3, a message counter, and recomputes the checksum;
    - MCTRL300/500/600/660 "Normal" writes 3 zero bytes at 0x02000100 (black, test pattern, freeze), so unfreezing works;
    - MCTRL4K uses the VX4S frames;
    - VX1000/VX16S/VX Pro/J6/UHD Jr and VX600 have their own frames and profiles;
    - the module's VX6S frames are all identical, so VX6S isn't covered.
  - **NovaStar COEX** (MX40 Pro, KU20, CX80 Pro): HTTP port 8001, `PUT /api/v1/screen/output/displaymode {"value": 0|1|2}` (normal, blackout, freeze); brightness 0-1 with `screenIdList`; no test pattern command.
  - **Brompton Tessera:** HTTP `GET /api/override/{freeze|blackout|test-pattern}/enabled?set=true|false`, `test-pattern/type?set=<type>`, and `/api/output/global-colour/brightness?set=` (nits).
  - **Megapixel HELIOS:** POST JSON to `/api/v1/public` (`dev.display.blackout|freeze|brightness`, `dev.ingest.testPattern.enabled|type`).
  - Every command has a `confidence` field that the UI shows. **Nothing has been tested on real hardware yet.**

## Not yet verified (top priorities for real-world testing)
1. LED commands marked partial or assumed, on real Brompton, HELIOS and NovaStar hardware.
2. Starting and stopping Unreal through a real listener after a password login. The 5.8 handshake and login replies are verified, but a full start was only tested against the fake QUIC listener. The TCP protocol is untested against real 5.2/5.3 listeners.
3. Python in a real editor: the sub-level visibility getter, and an actual Take Recorder recording (`start_recording`).
4. Concert log patterns for the multi-user client count.
5. Opening an Operator Board config in the Switchboard UI itself on 5.2–5.7. 5.8 was checked through Switchboard's own loader, not its window.

## Conventions
- Keep Python 3.9 compatible: no `match`, no runtime `X | Y` unions, no walrus.
- The README lists shipped features only; ideas go in `docs/ROADMAP.md`.
- Don't put model identifiers in commits or docs.
- Before launching throwaway Unreal instances to test against:
  - use Remote Control ports no stage device uses (30050+);
  - check they're free (`netstat -ano`);
  - confirm the answering PID is the probe's own before sending any change.

  A probe once moved the nDisplay root in the owner's live session.
