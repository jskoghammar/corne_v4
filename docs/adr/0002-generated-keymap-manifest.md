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
- The manifest lists **only signalled layers**. Base has no entry event, so it
  is not a binding target; keylens treats its drawing as an ordinary SVG. The
  drawing list is therefore not the manifest — it comes from `--layer-names`.
- The manifest carries a commit sha so a stale overlay is visible rather than
  silently wrong. That replaces keylens' branch pin.
