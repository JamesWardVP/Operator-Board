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

### NovaStar MCTRL (`novastar_mctrl`) and VX / NovaPro (`novastar_vx`)
- Binary protocol over TCP 5200. VX Pro series units listen on 15200.
- Source: `bitfocus/companion-module-novastar-controller` (`choices.js`, `actions.js`).
- The checksum is the sum of every byte after `55 AA`, plus `0x5555`, written as little-endian uint16. Operator Board appends it automatically (`"checksum": "novastar"`).
- **Freeze, Black and Normal display modes:** from source. Freeze and Black are a single display mode on these units, so turning on one cancels the other.
- **MCTRL Normal frame:** corrected to `…00 01 00 02 01 00 00`. This reproduces the published checksum `D6 59`.
- **Test patterns:** built with the module's runtime frame layout (byte 3 = `00`). Red is verified against the published checksum; the other patterns follow the same layout.
- **Brightness:** `makeBrightnessCommand` from source; the value is 0–255.
- VX600, VX1000 and VX6S have model-specific frames in the Companion module. Use command overrides if your unit ignores the generic frames.

### Brompton Tessera (`brompton_tessera`)
- HTTP GET `http://<ip>/api/<path>?set=<value>`.
- Source: `bitfocus/companion-module-brompton-tessera` (`apiKeys.js`).
- Paths come from source: `override/freeze/enabled`, `override/blackout/enabled`, `override/test-pattern/enabled`, `override/test-pattern/type`, and `output/global-colour/brightness` (nits, 0–10000).
- The `true`/`false` value format is **assumed**.

### Megapixel HELIOS (`megapixel_helios`)
- REST API: `PATCH /api/v1/public` (firmware 24.11+). Basic or Digest auth via the `username` and `password` options.
- Endpoints come from the PepperDash Megapixel plugin's documentation.
- Blackout and brightness bodies follow the `dev.display.*` tree and are **partial**.
- Freeze and test pattern bodies are **assumed**.

## Command format

```jsonc
// HTTP
{"method": "PATCH", "path": "/api/v1/public", "json": {"dev": {"display": {"blackout": true}}}}
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
