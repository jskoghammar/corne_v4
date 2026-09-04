#!/usr/bin/env python3
"""Generate keymap-drawer/layers.json from the keymap, or check it.

The keymap and keylens used to share a mapping table maintained by hand in two
repos. It drifted. This derives the mapping from a single parse of a single file
so index, display-name, drawing and signal cannot disagree.

Convention: layer index N emits a bare F13+N on entry, and releasing any layer
taps the index-0 signal (F13) so a consumer can hide on release rather than time
out. Base is therefore a normal manifest row, not an omission.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KEYMAP = REPO / "config" / "corne.keymap"
OUT = REPO / "keymap-drawer" / "layers.json"
SIGNAL_BASE = 13


def slug(name: str) -> str:
    lowered = name.lower().replace(" ", "-")
    return "".join(c for c in lowered if c.isalnum() or c in "_-")


def parse(keymap_text: str, keymap_name: str) -> list[dict]:
    layer_index = {
        m.group(1): int(m.group(2))
        for m in re.finditer(r"#define\s+LAYER_(\w+)\s+(\d+)", keymap_text)
    }
    signal = {
        m.group(1): m.group(2)
        for m in re.finditer(r"#define\s+SIG_(\w+)\s+(F\d+)", keymap_text)
    }
    # Which signal each layer is actually bound with, from the &lyr call sites.
    bound = {
        m.group(1): m.group(2)
        for m in re.finditer(r"&lyr\s+LAYER_(\w+)\s+SIG_(\w+)", keymap_text)
    }

    keymap_node = re.search(r"keymap\s*\{(.*)\n\s*\};", keymap_text, re.S)
    if not keymap_node:
        sys.exit("error: no keymap node found")

    layers = []
    for order, m in enumerate(
        re.finditer(r'display-name\s*=\s*"([^"]+)"', keymap_node.group(1))
    ):
        name = m.group(1)
        key = name.upper().replace(" ", "_")
        index = layer_index.get(key, order)
        entry = {
            "index": index,
            "name": name,
            "svg": f"img/{keymap_name}-{slug(name)}.svg",
            "signal": signal.get(key),
        }
        # Base is not entered through &lyr -- its signal is tapped on the way
        # out of every other layer -- so it has no call site to check.
        if index > 0:
            entry["boundSignal"] = bound.get(key)
        layers.append(entry)
    return layers


def check(layers: list[dict], keymap_text: str) -> list[str]:
    problems = []
    for layer in layers:
        index, name = layer["index"], layer["name"]
        expected = f"F{SIGNAL_BASE + index}"

        if index == 0:
            if layer.get("signal") != expected:
                problems.append(
                    f"{name}: resting layer implies {expected}, "
                    f"but SIG_{name.upper()} is {layer.get('signal')!r}"
                )
            # The resting signal is tapped after &mo is released, so the
            # overlay hides on release instead of waiting out a timeout.
            if not re.search(r"macro_release[^;]*?>\s*,\s*<&macro_tap\s+&kp\s+SIG_BASE",
                             keymap_text, re.S):
                problems.append(
                    f"{name}: lyr does not tap SIG_BASE after releasing the layer"
                )
            continue

        declared = layer.get("signal")
        bound_to = layer.get("boundSignal")
        if declared != expected:
            problems.append(
                f"{name}: layer index {index} implies {expected}, "
                f"but SIG_{name.upper()} is {declared!r}"
            )
        if bound_to is None:
            problems.append(f"{name}: no '&lyr LAYER_{name.upper()} SIG_...' binding found")
        elif bound_to != name.upper():
            problems.append(
                f"{name}: bound with SIG_{bound_to}, expected SIG_{name.upper()}"
            )
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="verify only, write nothing")
    ap.add_argument("--commit", default=None, help="commit sha to record")
    ap.add_argument(
        "--layer-names",
        action="store_true",
        help="print every layer's display name, one per line (the drawing list)",
    )
    args = ap.parse_args()

    layers = parse(KEYMAP.read_text(), KEYMAP.stem)

    if args.layer_names:
        for layer in layers:
            print(layer["name"])
        return 0

    problems = check(layers, KEYMAP.read_text())
    if problems:
        print("layer signal check failed:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    if args.check:
        print(f"ok: {len(layers)} layers, signals match their indices")
        return 0

    commit = args.commit
    if commit is None:
        try:
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=REPO,
                capture_output=True, text=True, check=True,
            ).stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            commit = "unknown"

    # Every layer ships, index 0 included: keylens reads the index-0 row as the
    # resting layer, using its signal to dismiss the overlay and deliberately
    # leaving its SVG unbound. Omitting the row is not a crash -- keylens falls
    # back to timing overlays out -- which makes it exactly the kind of silent
    # degradation the manifest exists to prevent.
    published = [
        {k: v for k, v in layer.items() if k != "boundSignal"} for layer in layers
    ]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"commit": commit, "layers": published}, indent=2) + "\n")
    print(f"wrote {OUT.relative_to(REPO)} ({len(published)} layers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
