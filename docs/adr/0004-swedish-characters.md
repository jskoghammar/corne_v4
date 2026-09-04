# Swedish characters are positional, and native where they can be

å, ä and ö have to be typed on a Mac set to British-PC, from a fleet where only
one keyboard is programmable.

**Position** follows a Swedish keyboard: å right of P, then ö and ä right of L.
That was already the idea behind the previous `⌘[`/`⌘;`/`⌘'` bindings and it was
the right one — the mistake was the modifier. `⌘[` is Back and `⌘M` is Minimize,
so typing Swedish had been costing browser navigation and window minimising
system-wide.

**On the Corne** the characters are emitted natively, so they work with no
application running. British-PC gives å directly as `⌥A`, but ä and ö exist only
behind the `⌥U` dead key — and Option must be *released* before the following
letter, or you get a diaeresis and an å. A macro does that reliably; fingers do
not.

**On the bare keyboards** BetterTouchTool maps right-Option + `[` `;` `'` to the
same three characters. Right-Option is the AltGr idiom every European layout
already uses, and it costs nothing — left Option keeps `"`, `…` and `æ`.

This split is ADR 0001's rule applied: a character is pure keystrokes, so the
firmware should not need an application to produce one. Window positioning is
something only the OS can do, so there the Corne asks rather than acts.

## Consequences

- The Corne types Swedish with BetterTouchTool uninstalled. The other keyboards
  do not.
- Row 1 column 11 stops being shift on Sym, and Base taps apostrophe there. The
  "always shift" rule in `agents.md` is written with those exceptions.
- The Flow85 carries an Option/Command swap, which moves right-Option off its
  AltGr key. If no key right of its space bar produces right-Option, the
  fallback is plain left-Option at the cost of those three characters.
