# macOS configuration lives in mac_setup

Three applications complete this keyboard's behaviour: Rectangle positions
windows, BetterTouchTool types Swedish characters on the non-programmable
keyboards, Homerow drives the UI. `mac_setup` installed all three from its
Brewfile and configured none of them, while `corne_v4` carried exported copies
of two of those configs — both stale, and byte-identical duplicates of copies
that were *also* sitting unused in `mac_setup/config`.

Configuration now lives with installation, in `mac_setup`, applied by scripts
rather than recorded as prose. The enforcement is that repo's existing promise:
it is rerunnable, so re-running it reconciles the machine. A checked-in export
tells you that you have drifted. A rerunnable installer undrifts you.

`corne_v4` keeps firmware and the generated manifest, and nothing else.

## Consequences

- Rectangle has no config at all — it runs factory defaults, and `apps.sh`
  deletes custom shortcut keys so it falls back to them. See ADR 0001.
- The per-device Option/Command swap is a `defaults` write keyed by vendor and
  product id, not a manual step in a heredoc. A new keyboard needs its own entry
  and will otherwise be silently unswapped.
- These apps rewrite their preferences when they quit, which would clobber
  anything written underneath them, so `apps.sh` quits them first.
- BetterTouchTool still needs a click; it has no import CLI. The preset keeps
  the original trigger UUIDs so importing updates in place instead of
  duplicating.
