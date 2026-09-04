# One keyboard vocabulary across the fleet

The Corne is programmable and the rest of the fleet is not, so it was tempting
to give the Corne a richer vocabulary — abstract modifier tiers that only
Rectangle understood — and let the bare keyboards make do. We did that, and it
rotted. The firmware encoded a shortcut table that nothing enforced; when
Rectangle's bindings changed, six of sixteen chords went dead, two began opening
Mission Control, and the display move we relied on was left bound to nothing.

Every Corne binding must now emit a chord a bare keyboard can also send. The
Corne is an *ergonomic* advantage, not a *capability* advantage: it makes the
same chord cheaper to press, never a different chord. Rectangle therefore runs
on its factory `⌃⌥` defaults, and the Corne holds `⌃⌥` as a real modifier
instead of signalling a tier for something else to decode.

The rule behind it: **the firmware does what only the firmware can do, and the
host does the rest.** A character is pure keystrokes, so å ä ö are emitted
natively by the Corne and work with no application running. Window positioning
is something only macOS can perform, so the Corne asks for it in the same words
any keyboard would. This resolves the apparent conflict with `agents.md`'s
"rely as little as possible on OS configurations" — the rule is not *never
configure the OS*, it is *don't configure the OS for what the firmware can do
unaided*.

## Consequences

- Quarters, resize and centring are reachable from every keyboard instead of
  being Corne-only, but each costs a labelled Nav key rather than a spatial
  chord.
- Muscle memory transfers between keyboards, which is the point.
- Rectangle's bindings can change without the firmware caring, because the
  firmware no longer encodes them.
