# Roadmap: potential future features

These are ideas, not commitments. The README lists only what is available today.

## LED processors
- More processor profiles: Colorlight (X/Z series, KS), Linsn, Absen and Unilumin controller platforms, ROE Evision, and NovaStar H-series.
- NovaStar VX6S frames, VX test patterns, and COEX test patterns.
- Read processor state back (frozen, blackout, temperature, link status) and show it on the dashboard.
- Processor presets: recall and save.
- Genlock and frame-rate status from processors.
- Per-processor zones, such as wall versus ceiling freeze.

## Switchboard parity
- Watch the open Switchboard config for changes made in Switchboard and reload automatically.
- Edit more Switchboard settings (per-device command lines, roles, logging, Insights) from Operator Board.
- Set Switchboard's last-opened config (`user_settings.json`) from Operator Board.
- Changelist / build sync and "Build" actions as in Switchboard.
- Full Unreal log streaming per device.
- nDisplay Monitor parity: sync topology, flip mode, mosaic and PresentMode warnings.
- Live Link Face / OSC / Shogun / Vicon device types for takes, as in Switchboard.
- Automatic device discovery on the network.
- Redeploying the listener to nodes.

## Multi-user
- Query session and client lists directly from the server instead of parsing logs.
- Restoring archived sessions and selecting from existing sessions.

## Levels & scene control
- Runtime data-layer switching on render nodes.
- Animated camera transitions between saved positions (interpolated moves).
- Level snapshot integration (Level Snapshots plugin).
- Lighting presets that combine sub-levels, data layers and property values into one button.
- Sequencer playback controls (play, stop, scrub) for virtual-production sequences.

## Takes
- Timecode-stamped take log.
- Take metadata export to editorial (ALE / EDL / CSV templates).
- Integration with external recorders (Atomos, AJA Ki Pro, Blackmagic HyperDeck).

## Control surfaces
- Companion module (native) published to the Bitfocus store, with feedback and variables.
- X-keys, Loupedeck, and Stream Deck + dials (brightness, exposure); testing on real MIDI hardware.
- Per-key feedback states beyond on/off. (Pictures on keys are a next priority, in `FUTURE_UPDATES.md` beside the repo.)

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
- File association for `.opboard` and `.opproject` in the installer.
- Signed releases (SignPath Foundation declined for now; re-apply later, or the Microsoft Store).
- Auto-update check against GitHub Releases.
- Localisation.
