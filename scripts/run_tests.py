# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Run every scripts/test_*.py and say which pass.

    python scripts/run_tests.py [name-filter]

Two kinds of test live here and nothing ran them together. Ones that only do
arithmetic load add-on modules by path (`_addon.py`) and run under Blender's
bundled interpreter in a second; ones that reach into Blender say so -- they
import bpy at the top or give a `blender -b` line -- and run in a background
Blender with `--factory-startup` (a profile add-on crashes it on some files,
docs/gotchas.md). A test passes on exit code 0, which is the contract every
test here keeps. Prints one line per test and a summary; exit 1 if any failed.
"""

import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BLENDER_DIR = os.path.dirname(os.path.dirname(HERE))
BLENDER = os.path.join(BLENDER_DIR, "blender.exe")
VERSIONS = sorted((d for d in os.listdir(BLENDER_DIR) if re.fullmatch(r"\d+\.\d+", d)),
                  key=lambda v: tuple(map(int, v.split("."))))
PYTHON = os.path.join(BLENDER_DIR, VERSIONS[-1], "python", "bin", "python.exe")


def needs_blender(path):
    text = open(path, encoding="utf-8", errors="replace").read()
    return bool(re.search(r"^import bpy|^\s*import addon_utils|blender -b", text, re.M))


def main():
    pattern = sys.argv[1] if len(sys.argv) > 1 else ""
    tests = sorted(f for f in os.listdir(HERE)
                   if f.startswith("test_") and f.endswith(".py") and pattern in f)
    results = []
    for name in tests:
        path = os.path.join(HERE, name)
        if needs_blender(path):
            cmd, kind, limit = [BLENDER, "-b", "--factory-startup", "--python", path], "blender", 1800
        else:
            cmd, kind, limit = [PYTHON, path], "python", 600
        started = time.perf_counter()
        try:
            got = subprocess.run(cmd, capture_output=True, text=True, timeout=limit,
                                 errors="replace", cwd=os.path.dirname(HERE))
            code, out = got.returncode, (got.stdout + got.stderr)
        except subprocess.TimeoutExpired:
            code, out = "timeout", ""
        took = time.perf_counter() - started
        tail = [ln for ln in out.splitlines() if ln.strip() and "blendkit" not in ln][-1:] or [""]
        status = "PASS" if code == 0 else f"FAIL ({code})"
        results.append((name, status, kind, took, tail[0][:110]))
        print(f"{status:12s} {name:34s} {kind:7s} {took:6.1f}s  {tail[0][:110]}", flush=True)
    failed = [r for r in results if r[1] != "PASS"]
    print(f"\n{len(results) - len(failed)} of {len(results)} passed"
          + (": failed " + ", ".join(r[0] for r in failed) if failed else ""), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
