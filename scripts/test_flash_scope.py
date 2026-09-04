#!/usr/bin/env python3
"""Tests for the flash-scope decision in flash_firmware.py.

Run: python3 scripts/test_flash_scope.py

The decision these cover is "may we skip flashing a half?", so every failure
mode here is biased one way on purpose: when anything is unknown or malformed
we flash, because a skipped-but-stale peripheral is a confusing broken
keyboard while a redundant flash is a few seconds.
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import flash_firmware as ff

LEFT_OLD = "a" * 64
LEFT_NEW = "b" * 64
RIGHT = "c" * 64
LEFT_NAME = "primary (left)"
RIGHT_NAME = "secondary (right)"
BOTH = [LEFT_NAME, RIGHT_NAME]

failures: list[str] = []


def candidates(left_digest: str, right_digest: str):
    return [
        (LEFT_NAME, "left", {}, Path("l.uf2"), left_digest),
        (RIGHT_NAME, "right", {}, Path("r.uf2"), right_digest),
    ]


def check(label: str, got, want) -> None:
    if got == want:
        print(f"PASS  {label}")
        return
    failures.append(label)
    print(f"FAIL  {label}\n        got={got!r}\n        want={want!r}")


def split(plan, skipped):
    return [row[0] for row in plan], [row[0] for row in skipped]


def main() -> int:
    flashed_at = "2026-09-04T08:30:00"
    state = {
        "left": {"uf2_sha256": LEFT_OLD, "flashed_at": flashed_at},
        "right": {"uf2_sha256": RIGHT, "flashed_at": flashed_at},
    }

    check(
        "no state -> flash both",
        split(*ff.plan_sides(candidates(LEFT_NEW, RIGHT), {}, "auto")),
        (BOTH, []),
    )
    check(
        "keymap-only change -> flash left, skip right",
        split(*ff.plan_sides(candidates(LEFT_NEW, RIGHT), state, "auto")),
        ([LEFT_NAME], [RIGHT_NAME]),
    )
    check(
        "nothing changed -> skip both",
        split(*ff.plan_sides(candidates(LEFT_OLD, RIGHT), state, "auto")),
        ([], BOTH),
    )
    check(
        "scope=both overrides a clean match",
        split(*ff.plan_sides(candidates(LEFT_OLD, RIGHT), state, "both")),
        (BOTH, []),
    )
    check(
        "peripheral image changed -> flash both",
        split(*ff.plan_sides(candidates(LEFT_NEW, "d" * 64), state, "auto")),
        (BOTH, []),
    )

    for label, bad in (
        ("null entry", None),
        ("missing digest", {"flashed_at": "x"}),
        ("wrong digest type", {"uf2_sha256": 123}),
    ):
        check(
            f"malformed state ({label}) -> flash both",
            split(*ff.plan_sides(candidates(LEFT_OLD, RIGHT), {"left": bad, "right": bad}, "auto")),
            (BOTH, []),
        )

    _, skipped = ff.plan_sides(candidates(LEFT_NEW, RIGHT), state, "auto")
    check("skip reason reports when it was flashed", skipped[0][1], flashed_at)
    _, skipped = ff.plan_sides(candidates(LEFT_NEW, RIGHT), {"right": {"uf2_sha256": RIGHT}}, "auto")
    check("skip reason falls back when timestamp missing", skipped[0][1], "an earlier run")

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        check("missing state file -> {}", ff.read_flash_state(tmp / "absent.json"), {})
        (tmp / "corrupt.json").write_text("{not json", encoding="utf-8")
        check("corrupt state file -> {}", ff.read_flash_state(tmp / "corrupt.json"), {})
        (tmp / "list.json").write_text("[1, 2, 3]", encoding="utf-8")
        check("non-dict state file -> {}", ff.read_flash_state(tmp / "list.json"), {})
        ff.write_flash_state(tmp / "state.json", state)
        check("state round trip", ff.read_flash_state(tmp / "state.json"), state)

        blob = (tmp / "blob.bin")
        blob.write_bytes(b"corne" * 1000)
        check(
            "sha256_file matches hashlib",
            ff.sha256_file(blob),
            hashlib.sha256(b"corne" * 1000).hexdigest(),
        )

    print()
    if failures:
        print(f"{len(failures)} FAILURES: {', '.join(failures)}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
