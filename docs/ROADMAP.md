# Roadmap: potential future features

These are ideas, not commitments. The README lists only what is available today.

## LED processors
- More processor profiles: Colorlight (X/Z series, KS), Linsn, Absen and Unilumin controller platforms, ROE Evision, NovaStar COEX (MX40 Pro HTTP API), and NovaStar H-series.
- Model-specific NovaStar frames (VX600 / VX1000 / VX6S display modes, VX test patterns).
- Read processor state back (frozen, blackout, temperature, link status) and show it on the dashboard.
- Processor presets: recall and save.
- Genlock and frame-rate status from processors.
- Per-processor zones, such as wall versus ceiling freeze.

## Switchboard parity
- SwitchboardListener authentication for engine versions that require it.
- Changelist / build sync and "Build" actions as in Switchboard.
- Full Unreal log streaming per device.
- nDisplay Monitor parity: sync topology, flip mode, mosaic and PresentMode warnings.
- Live Link Face / OSC / Shogun / Vicon device types for takes, as in Switchboard.
- Automatic device discovery on the network.
- Redeploying the listener to nodes.

## Multi-user
- Query session and client lists directly from the server instead of parsing logs.
- Session archive and restore, persistence, and selecting from existing sessions.

## Levels & scene control
- Runtime data-layer switching on render nodes.
- Animated camera transitions between saved positions (interpolated moves).
- Level snapshot integration (Level Snapshots plugin).
- Lighting presets that combine sub-levels, data layers and property values into one button.
- Sequencer playback controls (play, stop, scrub) for virtual-production sequences.

## Takes
- Timecode display and timecode-stamped take log.
- Take metadata export to editorial (ALE / EDL / CSV templates).
- Integration with external recorders (Atomos, AJA Ki Pro, Blackmagic HyperDeck).

## Control surfaces
- Companion module (native) published to the Bitfocus store, with feedback and variables.
- OSC input server.
- MIDI controllers, X-keys, Loupedeck, and Stream Deck + dials (brightness, exposure).
- Icons on Stream Deck keys; per-key feedback states beyond on/off.

## File sync & revision control
- Scheduled syncs, conflict summaries, and per-node progress bars.
- FreeFileSync RealTimeSync monitoring.
- Plastic SCM / Unity Version Control and SVN providers.

## Analytics
- Unreal frame time, FPS and GPU frame time per node (stat unit) via Remote Control or Unreal Insights.
- Alerting thresholds (GPU temperature, dropped frames) with on-screen and Stream Deck alerts.
- Long-term logging to disk and session reports.

## General
- Role-based "lock" mode, so only Freeze, Blackout and Record are available during takes.
- Multiple stage profiles and a quick stage switcher.
- Installer / packaged executable for Windows.
- Web/tablet remote UI served by the API server.
- Localisation.
