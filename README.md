# Operator-Board

An easy-to-use, operator-friendly Virtual Production front end for Unreal Engine **Switchboard**, built for running Unreal Engine on LED volume stages.

Operator Board talks to the same **SwitchboardListener** and **Remote Control API** that Switchboard uses. It can run alongside Switchboard or on its own, and it presents everyday stage operations as large, readable controls.

**Version:** 0.1.0

## Supported Unreal Engine versions

| Unreal Engine | Supported |
|---|---|
| 5.2 | ✅ |
| 5.3 | ✅ |
| 5.4 | ✅ |
| 5.5 | ✅ |
| 5.6 | ✅ |
| 5.7 | ✅ |
| 5.8 | ✅ |

Operator Board detects the engine version from `Engine/Build/Build.version` when you choose an engine folder.

## Features

### Operate dashboard
- **LED wall:** one-touch **Freeze**, **Blackout** and **Wall Normal**, a test pattern picker, a live status banner ("WALL FROZEN") and a status chip for each processor.
- **Unreal instances:** connect, start and stop every instance, a group, or a single device, with live state for each one.
- **Multi-user:** name and start the server, and see how many clients are connected and who they are.
- **Project & level:** pick the project and level and load it.
- **Takes:** slate and take display with a big Record button.
- **Camera positions:** one-touch recall of saved positions.
- **Stage hotkeys** work from any page: F9 Freeze · F10 Blackout · F11 Wall normal · F12 Record · Ctrl+Shift+S Start all.

### LED processor control
Choose the processor type, enter its IP address and port (the same way you would in Bitfocus Companion), and the wall controls drive every processor at once.

| Processor | Connection | Freeze | Blackout | Test patterns | Brightness |
|---|---|---|---|---|---|
| Brompton Tessera (SX40 / S8 / M2 / T1 / G1) | HTTP :80 | ✅ | ✅ | ✅ | ✅ |
| Megapixel HELIOS | HTTP :80 (REST) | ✅ | ✅ | ✅ | ✅ |
| NovaStar MCTRL (300 / 600 / 660 / 4K / R5) | TCP :5200 | ✅ | ✅ | ✅ | ✅ |
| NovaStar VX / NovaPro | TCP :5200 | ✅ | ✅ | – | ✅ |
| Generic HTTP / TCP / UDP / OSC | user defined | ✅ | ✅ | ✅ | ✅ |
| Simulator (no hardware, for training) | – | ✅ | ✅ | ✅ | ✅ |

Each command is labelled in the app with how it was verified. Some commands come straight from the published Companion module sources; others still need a check against your processor's firmware. See [docs/LED_PROCESSORS.md](docs/LED_PROCESSORS.md). You can override any command per processor, and you can add new processor types as JSON profiles without changing any code.

### Unreal instances (Switchboard functionality)
- Editor and nDisplay node devices launched through SwitchboardListener (port 2980), using the same command-line flags as Switchboard.
- Device groups that start and stop together; start, stop or restart everything.
- Import devices from an existing Switchboard config file.
- A per-device log with the launch command line and process events.

### Multi-user
- Start and stop the multi-user (Concert) server locally or on any listener machine, with a choice of console or Slate server.
- Name the server and session; editors and nodes join automatically.
- Live connected-client count and names, taken from the server log. The log patterns are configurable.

### Levels, sub-levels & cameras
- Manage several projects and switch between them; find `.uproject` files on disk.
- List a project's levels from its Content folder, or from the editor through Remote Control.
- Load a level in the editor, change level on nodes at runtime, or relaunch the nDisplay nodes on the new level.
- Show or hide **streaming sub-levels** (for example, lighting setups) in the editor and on render nodes, and toggle **World Partition data layers**.
- **Saved camera positions:** capture and recall the editor viewport or any camera / nDisplay root actor.

### Takes
- Slate, take number and description, with auto-increment.
- Drives **Take Recorder** in the primary editor.
- Persistent take log with Good, Circle and NG ratings and notes; CSV export.

### Remote objects
- Browse **Remote Control Presets** and read or set exposed properties and call exposed functions on the editor, the nodes or everything.
- Actor browser: list actors, inspect their properties, and read or set values.

### DLSS
- DLSS Super Resolution mode, Frame Generation, Ray Reconstruction, Reflex and Screen Percentage, applied to the editor, the nodes or all instances.
- Plugin detection. Controls are defined in `dlss.json`, so a change in the plugin's API only needs a JSON edit.

### File sync
- Sync jobs using **FreeFileSync** batch jobs, **Perforce**, **Git**, **Robocopy** or a custom command.
- Run on this machine or on render nodes through their listeners; keeps a result history and output.

### Analytics
- Live CPU, GPU, GPU clock, GPU temperature and memory for each running instance, plus multi-user client and running-instance counts.
- Graphs over 1 min, 5 min, 15 min or 1 hour, a raw sample table, the last raw listener payload, and CSV export.

### Control surfaces
- **Elgato Stream Deck** driven directly (Mini, 15-key / MK.2, XL, +, Neo), with multiple pages and live key colours.
- A default layout is included. You can edit layouts in the app and **save them to or load them from an external JSON file** to use on another machine.
- An on-screen virtual deck with a live mode.
- **Local HTTP API** for **Bitfocus Companion** (Generic HTTP module) or any HTTP-capable surface, with an optional token.

### Configuration
- One JSON configuration file for the whole stage, with import and export.
- Headless mode (`--headless`): the API and Stream Deck only, with no window.

## Installation

Requires Python 3.9 or newer.

```bash
git clone https://github.com/JamesWardVP/Operator-Board.git
cd Operator-Board
pip install -r requirements.txt
python -m operator_board
```

Optional direct Stream Deck support:

```bash
pip install streamdeck pillow
```

On Windows, quit the Elgato Stream Deck app first, because only one program can own the device.

**Running with Switchboard's own Python:** `scripts\run_with_switchboard_python.bat "C:\Program Files\Epic Games\UE_5.6"` uses the Python environment that ships with Switchboard, so nothing extra is installed. PySide2 (UE 5.2/5.3) and PySide6 are both supported.

## Requirements in Unreal

| Feature | Needs |
|---|---|
| Launch / stop instances, sync on nodes, analytics | SwitchboardListener running on each machine |
| Levels, sub-levels, cameras, takes, objects, DLSS | **Remote Control API** plugin (instances are launched with `-RCWebControlEnable`) |
| Level loading, sub-levels, data layers, viewport cameras, Take Recorder | **Python Editor Script Plugin** in the editor |
| DLSS page | NVIDIA DLSS / Streamline plugins |

## Development

```bash
pip install pytest
python -m pytest
```

The test suite includes fake SwitchboardListener and HTTP peers, and an offscreen UI smoke test. The project layout is described in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
