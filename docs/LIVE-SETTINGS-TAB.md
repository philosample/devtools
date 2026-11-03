# Open Live Settings on Tempo & MIDI

`ax live-settings-tab` is a narrow macOS Accessibility action for one exact
Ableton Live process and one exact Set window. The default command is a
read-only preflight. It verifies the process PID, bundle identifier, launch
generation, the exact named Live window on the current Desktop, and the
`Live > Settings...` AX menu item. If a unique Settings window is already open,
it also checks for the `Tempo & MIDI` radio button with identifier
`midiTabButton`.

First inspect the exact target window. Replace the example PID and title with
the current values from the designated operator's process/window observation:

```sh
python3 -m codex_devtools ax live-settings-tab \
  --pid 12345 \
  --expected-bundle-id com.ableton.live \
  --window-title "Exact visible Set window title"
```

Review the returned `target.process_generation` token. To open Settings and
press the Tempo & MIDI tab, repeat with that token and `--execute`:

```sh
python3 -m codex_devtools ax live-settings-tab \
  --pid 12345 \
  --expected-bundle-id com.ableton.live \
  --window-title "Exact visible Set window title" \
  --expected-generation PROCESS_GENERATION_FROM_INSPECTION \
  --execute
```

Execution checks that the exact named Live window is already on the current
Desktop before activating the same PID. It rechecks process generation and
foreground/focus before each AX press. It never invokes AppleEvents, keystrokes,
coordinates, Space switching, or permission prompts. It presses only the exact
`Live > Settings...` menu item and the unique Tempo & MIDI radio button whose identifier is
`midiTabButton`. AX traversal is bounded to 64 windows, 512 elements and depth
16 for Settings, and 128 elements and depth 8 for the menu. Every AX message has
a two-second timeout and readiness waits are bounded to five seconds. An
uncertain result is reported without retry.

The command reads back the tab radio button's selected value after its AX press.
It does not read or change any MIDI preference checkbox values. The coordinator
must inspect the complete Settings tree and verify the required checkbox values
independently. This is not Live-host acceptance, a MIDI isolation assertion, or
MIDI delivery evidence.

Focused source and command-boundary checks:

```sh
python3 -m unittest tests.test_live_settings_tab -v
swiftc -framework ApplicationServices -framework AppKit -framework CoreGraphics \
  codex_devtools/live_settings_tab.swift -o /tmp/codex-live-settings-tab-check
```

Those checks do not launch Live or perform an AX action. Native operation is
reserved for the designated desktop operator.
