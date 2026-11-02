# LED processor protocols

Operator Board drives LED processors through **protocol profiles**: JSON files in `operator_board/led/profiles/`. You can also add your own in `~/.operator_board/led_profiles/`. Each profile names a transport (`http`, `tcp`, `udp` or `osc`), a default port, and a set of commands. This mirrors how Bitfocus Companion modules connect: the operator enters a host or IP address and a port.

## Confidence levels

Every command carries a `confidence` value, which is shown in colour on the LED Processors page:

| Value | Meaning |
|---|---|
| `source` | Byte-for-byte or request-for-request match with the published Bitfocus Companion module source. NovaStar checksums are verified by tests. |
| `partial` | The endpoint or path is published, but the value format (for example `true`/`false`) is assumed. |
| `assumed` | Inferred from the vendor's data model. Verify against the vendor API docs. |
| `user` | A command override entered by the operator. |

**Test any amber or red command on your hardware before a show.**

## Status per processor

Checked in 0.8 against the Bitfocus Companion modules installed with Companion 4.2 (`%APPDATA%\companion\modules`): brompton-tessera 2.2, mvr-helios 2.3, novastar-controller 2.6 and novastar-coex 2.0. None of them has an identify / locate command.

### NovaStar MCTRL, VX and NovaPro (`novastar_mctrl`, `novastar_vx`, `novastar_vx1000`, `novastar_vx600`)
- Binary protocol over TCP 5200. VX Pro series units listen on 15200.
- Source: `companion-module-novastar-controller` 2.6.
- The checksum is the sum of every byte after `55 AA`, plus `0x5555`, written as little-endian uint16. Operator Board appends it automatically (`"checksum": "novastar"`). The module overwrites byte 3 with a message counter and recomputes the checksum, so byte 3 does not matter.
- **Display modes** (Normal, Freeze, Black) are from source, so they cancel each other. Each model family has its own frames:
  - MCTRL300 / 500 / 600 / 660 (`novastar_mctrl`): Normal writes three zero bytes at `0x02000100` (black, test pattern and freeze), so it also unfreezes and turns a test pattern off.
  - VX4S, NovaPro HD and MCTRL4K (`novastar_vx`).
  - VX1000, VX16S, VX Pro, J6 and NovaPro UHD Jr (`novastar_vx1000`).
  - VX600 (`novastar_vx600`).
  - VX6S is not covered: the module's three VX6S frames are identical apart from the message counter.
- **Test patterns** (MCTRL): from source. VX models have patterns in the module but no "pattern off", so none are built in.
- **Brightness:** `makeBrightnessCommand` from source; the value is 0-255 (MCTRL and VX4S family).

### NovaStar COEX (`novastar_coex`): MX40 Pro, KU20, CX80 Pro
- HTTP on port 8001. Source: `companion-module-novastar-coex` 2.0.
- `PUT /api/v1/screen/output/displaymode` with `{"value": 0|1|2}` (normal, blackout, freeze).
- Brightness: `PUT /api/v1/screen/brightness` with `{"brightness": 0-1, "screenIdList": [...]}`. The screen IDs come from `GET /api/v1/screen` (`data.screens[].screenID`), asked once, or from the processor's `screen_ids` option.
- No test pattern command (the module's action calls an API function that does not exist).
- If COEX VMP is open on another computer, the processor answers "device locked".

### Brompton Tessera (`brompton_tessera`)
- HTTP GET `http://<ip>/api/<path>?set=<value>`. Source: `companion-module-brompton-tessera` 2.2.
- `override/freeze/enabled`, `override/blackout/enabled`, `override/test-pattern/enabled` with `true` / `false`, `override/test-pattern/type` with the pattern name, and `output/global-colour/brightness` (nits, 0-10000).

### Megapixel HELIOS (`megapixel_helios`)
- The module POSTs JSON to `/api/v1/public`. Basic or Digest auth via the `username` and `password` options. Source: `companion-module-mvr-helios` 2.3.
- `dev.display.blackout`, `dev.display.freeze`, `dev.display.brightness` (0-100), `dev.ingest.testPattern.enabled` and `.type`.

## Command format

```jsonc
// HTTP
{"method": "POST", "path": "/api/v1/public", "json": {"dev": {"display": {"blackout": true}}}}
// TCP / UDP
{"hex": "55 AA 00 80 FE ...", "checksum": "novastar"}
{"text": "FREEZE 1\r\n"}
// OSC
{"address": "/wall/freeze", "args": [1]}
// Alias
{"ref": "normal"}
```

- Use `{value}` as a placeholder for brightness.
- `"value": {"min": 0, "max": 255, "format": "hex_byte" | "int" | "float"}` maps the operator's 0–100 % value onto the processor's native range.
- Supported command names: `freeze_on`, `freeze_off`, `blackout_on`, `blackout_off`, `test_pattern_off`, `brightness`, plus `test_patterns: [{id, label, commands: [...]}]`.
