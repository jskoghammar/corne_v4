# The keymap/keylens contract is generated, not maintained

keylens draws an overlay of the active layer, so it has to know which keystroke
means which layer. That mapping lived in two repos — the firmware picked a
sentinel keycode, keylens' settings bound it — with nothing connecting them. It
drifted, and drifted badly: keylens auto-assigned overlays to `⌃⌥`+arrow, the
namespace Rectangle owns, so snapping a window left popped up a keymap.

Layer index N now emits a bare `F13+N` on entry, and CI generates
`keymap-drawer/layers.json` from the same parse of the keymap that draws the
SVGs. Index, display name, drawing and signal come from one read of one file, so
they cannot disagree. keylens consumes that instead of a local hotkey table, and
a CI check asserts each layer's signal matches its index — drift fails the build
instead of the fingers.

Bare function keys are the whole point. macOS binds none of `F13`–`F24`, so
unlike the naked modifiers this replaces — five `RSHFT` taps trip Sticky Keys, a
naked `RGUI` tap opens the Start menu inside a Windows VM — nothing else can
react to them.

## Consequences

- Adding a layer needs no keylens change. It appears in the manifest.
- **Index 0 is the resting layer, and it is a normal manifest row.** Releasing
  any layer taps its signal (`F13`), so keylens hides the overlay on release
  instead of timing it out — every layer is a momentary hold, and entry alone
  never says how long the hold lasts. keylens uses that row's signal to dismiss
  and deliberately leaves its SVG unbound, so nothing draws Base at rest.
- Omitting the resting row does not fail: keylens falls back to timing overlays
  out. That silent degradation is exactly why the CI check covers index 0 too.
- The manifest carries a commit sha so a stale overlay is visible rather than
  silently wrong. That replaces keylens' branch pin.
