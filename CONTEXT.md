# Corne v4

The keyboard system: a ZMK split keyboard, the non-programmable keyboards used
alongside it, and the macOS applications that complete its behaviour.

## Language

### The fleet

**Fleet**:
Every keyboard used with this Mac — the Corne plus the bare keyboards.
_Avoid_: setup, my keyboards

**Bare keyboard**:
A keyboard in the fleet that cannot be programmed — the Lofree Flow85, the
Keytronic, the built-in Apple keyboard. It can only send literal chords.
_Avoid_: external keyboard, secondary keyboard

**Floor**:
The capabilities every keyboard in the fleet must provide: half snapping,
screen switch, Homerow. Anything outside the floor may be Corne-only.
_Avoid_: baseline, must-haves

### Actions

**Display move**:
Sending the focused window to another physical display.
_Avoid_: screen switch

**Space switch**:
Moving to another macOS Space.
_Avoid_: screen switch, desktop switch

**Swedish-positional**:
Placing å, ö and ä at the physical positions they occupy on a Swedish
layout — right of P, and the two keys right of L.

### Firmware and host

**Signal**:
A keystroke the firmware emits solely to tell a host application which layer
is active. It types nothing and no other application should react to it.
_Avoid_: emitter, sentinel

**Overlay**:
The keymap image keylens displays when it observes a signal.
_Avoid_: HUD, popup

**Manifest**:
The generated description of the keymap that host applications consume — layer
index, display name, drawing, and signal. Generated from the keymap, never
hand-edited.
_Avoid_: config, layers.json (that is its filename, not its name)

**Two-sided contract**:
A behaviour split between the firmware and a host application such that neither
side can detect when the other changes. The recurring failure mode in this
repo; see [[0001-one-keyboard-vocabulary]].
_Avoid_: integration, coupling

**Tier** _(rejected)_:
A scheme in which the Corne sent abstract modifier groups that only Rectangle
could interpret. Recorded so it is not reintroduced — a held `⌃⌥` modifier key
is not a tier, because the chord it sends means the same thing everywhere.
