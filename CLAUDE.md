# CLAUDE.md: project context for Claude Code

This file carries the full project brief and the decisions made so far, so work can continue in any Claude Code session (local or cloud) without re-explaining. Keep it updated when significant decisions are made.

## The brief (from the project owner)
Operator Board is a Virtual Production, operator-friendly extension to Unreal Engine **Switchboard**: an easily navigable UI for controlling UE virtual production.

1. **Primary:** at the push of a button, **freeze** and/or **blackout** the LED wall, or show a **test pattern** from the LED processor. The user selects their LED processor type and, if it is networked and configured, commands go straight to it. It must support all major / common LED processors and connect **the same way Bitfocus Companion does** (host/IP + port per module).
2. All standard Switchboard functionality on easy-to-read displays: connect to and start UE instances (editor, nDisplay nodes…); start, name and monitor a **multi-user server** (show the connected count); start and stop **groups** of instances.
3. Integration with **file sync / revision** setups such as FreeFileSync, and **analytics** for live instances (raw data and graphs). These go on **separate pages** from the main display.
4. Load and swap **levels** within a project, and if possible between **projects**.
5. A list of **instance-editable objects** controllable remotely.
6. A configuration page for **control surfaces (Stream Deck)** covering all standard operations (load or swap levels, start or stop multi-user, freeze, blackout…). It needs a **default config**, user customisation, and **save/load to an external file** for use on another machine.
7. An easy UI for creating, controlling and saving **takes**, as in Switchboard.
8. **DLSS** settings control when the project uses the DLSS plugin.
9. Toggle **sub-levels** (often used for lighting setups) and move between **saved camera locations**.
10. Keep a list of **potential future features**, but NOT in the README. The **README lists only currently available features and the supported UE versions (5.2 → 5.8)**.
11. Hosted on GitHub as `JamesWardVP/Operator-Board`.

## Current state (v0.2.2)
Every brief item has a first implementation. See README.md for the feature list, docs/ARCHITECTURE.md for structure, docs/LED_PROCESSORS.md for protocol research, and docs/ROADMAP.md for future ideas.

- Python + Qt (PySide6, with a PySide2 fallback through `operator_board/qt.py`), so it can also run in Switchboard's own Python venv.
- Run: `python -m operator_board` (`--headless`, `--no-hardware`, `--config path`, `--switchboard-config path`).
- Windows executable: `python packaging/build.py --hidapi` builds `dist/OperatorBoard/OperatorBoard.exe` (+ `OperatorBoardConsole.exe`) with PyInstaller; CI (`.github/workflows/build-windows.yml`) builds on every push and attaches the zip to `v*` tag releases.
- Work happens on the `Development` branch; `main` gets it through pull requests.
- License: MIT (`LICENSE`); bundled components in `THIRD_PARTY_NOTICES.md`. Code signing is being applied for through **SignPath Foundation** (free for OSS). Their conditions are why the README has the "Code signing policy" and "Privacy policy" sections and uninstall steps, and why every release links to them. When approved, add the SignPath signing step to `.github/workflows/build-windows.yml` (sign both exes before zipping), with credentials in GitHub Secrets. Do not add proprietary components: SignPath requires everything to be open source.
- README screenshots: `python docs/make_screenshots.py` renders `docs/images/*.png` from a simulated demo stage (needs a desktop session for fonts).
- Example stage config: `config/example_stage.json` (uses simulated LED processors).
- Tests: `python -m pytest`. The suite includes fake SwitchboardListener and HTTP servers, and an offscreen Qt smoke test (set `QT_QPA_PLATFORM=offscreen` on headless Linux).

## Key decisions & research findings
- **Single ActionRegistry** (`context.py`): the UI, Stream Deck, Companion HTTP API (`/api/action/<id>`) and hotkeys all invoke the same actions. Add new features as actions.
- **Listener protocol** mirrors Switchboard's `message_protocol.py`: null-terminated JSON, `start` / `kill` / `keepalive` / `get sync status`, program name `"unreal"`. **UE 5.4+ listeners use QUIC over UDP 2980 with a password** (TCP 2980 is closed); 5.2-5.3 use TCP. Checked against Switchboard 5.2/5.6/5.8 sources and a real 5.8 listener (handshake, `authenticate` reply, wrong-password rejection). Replies echo ids as FGuid strings, so compare ids with `protocol.same_id`. Program messages nest details under `process` (`uuid`, `pid`, `name`, `caller`) and every connection hears every program, so `caller` must be the device name and messages are filtered by it (`protocol.program_info`). The keepalive command is `"keep alive"`. The multi-user **server** must not get `-CONCERTSESSION` (it would create a project-less session that refuses every client; it is also recovered after a forced shutdown). nDisplay nodes need the cluster JSON extracted from the config asset and written to the node with `send file` (`devices/ndisplay.py`). Passwords are shared with Switchboard through Windows Credential Manager (`Switchboard_<host>`), verified by reading an entry written by Operator Board with Switchboard's own code. See docs/ARCHITECTURE.md.
- **Remote Control** (port 30010) is used for everything inside Unreal. Editor-only operations run Python through `ExecutePythonCommandEx` and return JSON via an `OPB_RESULT:` print marker. These need the Python Editor Script Plugin.
- **LED protocols** (researched against Bitfocus Companion module sources):
  - NovaStar: binary TCP 5200 (VX Pro: 15200). Checksum = sum(bytes[2:]) + 0x5555, as LE uint16. Display-mode frames come from source. The MCTRL "Normal" frame was corrected to `…00 01 00 02 01 00 00` (matches published checksum D6 59). Test-pattern frames use byte 3 = 00 (Red verified).
  - Brompton Tessera: HTTP `GET /api/override/{freeze|blackout|test-pattern}/enabled?set=…` and `/api/output/global-colour/brightness?set=` (nits). Paths come from source; the `true`/`false` values are assumed.
  - Megapixel HELIOS: `PATCH /api/v1/public` (from the PepperDash docs). The JSON bodies are partial or assumed.
  - Every command has a `confidence` field that the UI shows. **Nothing has been tested on real hardware yet.**
- Multi-user client counting parses server logs with configurable regexes. The exact Concert log wording has not been verified against a real server.
- DLSS controls are data-driven in `remote/dlss.json` (DLSSLibrary.SetDLSSMode, StreamlineLibraryDLSSG.SetDLSSGMode, plus cvars).
- **Switchboard configs** (`core/switchboard_config.py`, `core/stage.py`, Stage page): Operator Board opens/creates/saves the real Switchboard config files in Switchboard's own folder (`<Engine>/Plugins/VirtualProduction/Switchboard/Source/Switchboard/configs`, `SWITCHBOARD_CONFIG_DIR` from 5.6). Format checked against the 5.2, 5.6 and 5.8 sources and real 5.8 configs. Operator Board-only settings go in a `<name>.opboard` sidecar (never `.json`: Switchboard rglobs `*.json`). Only mapped keys are merged, so real configs round-trip unchanged. A config created by Operator Board was loaded by Switchboard 5.8's own `Config` + `DeviceManager` successfully; Switchboard dropped the per-device listener `port` on save (it is per device type), so the sidecar keeps it.

## Not yet verified (top priorities for real-world testing)
1. LED commands marked partial or assumed, on real Brompton, HELIOS and NovaStar hardware.
2. Starting/stopping Unreal through a real listener after a successful password login (5.8 handshake and login replies are verified; a full start was only tested against the fake QUIC listener), and the TCP protocol against real 5.2/5.3 listeners.
3. Python snippets in `remote/scripts.py` (sub-level visibility getter, Take Recorder API) inside a real editor.
4. Concert log patterns for the multi-user client count.
5. Opening an Operator Board-created config in the Switchboard UI itself on 5.2–5.7 (5.8 was checked through Switchboard's own loader, not by clicking through its window).

## Conventions
- Keep Python 3.9 compatible: no `match`, no `X | Y` type unions at runtime, no walrus.
- Core packages must not import Qt; only `ui/` and `qt.py` do.
- The README lists shipped features only; ideas go in `docs/ROADMAP.md`.
- Do not put model identifiers in commits or docs.
