"""Re-copy the web player into the add-on, and prove it still inlines.

    python scripts/sync_player.py          # check only, exit 1 if stale
    python scripts/sync_player.py --write  # copy it across

`addon/vecbake/preview/player.js` is a copy of `lerpa/src/player.js`, inlined
into every `.preview.html` so the page runs from disk with no server. Nothing
in the build keeps the two in step, so a bug fixed in the npm package reaches
the preview only when someone remembers this file exists. Run it in CI, or
before tagging the add-on.

The check is deliberately two-part. Byte equality catches the copy going stale,
which is the common case. Re-running the module-syntax strip catches the case
that actually breaks a preview silently: the player growing an `import`, which
would produce an HTML file that opens to a blank page with a console error no
animator will think to look for.
"""

import importlib.util
import os
import shutil
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(ROOT, "addon", "vecbake")
SRC = os.path.join(ROOT, "lerpa", "src", "player.js")
DST = os.path.join(ADDON, "preview", "player.js")


def _load_emit_preview():
    """Import emit_preview without running the add-on's __init__.

    `vecbake/__init__.py` is the Blender entry point and imports bpy, so a
    plain import fails outside Blender -- which is exactly where this script
    is meant to run. A stand-in package with the right __path__ lets the
    module's own relative imports resolve; everything it reaches
    (emit_json -> ir) is deliberately bpy-free.
    """
    shim = types.ModuleType("_vb")
    shim.__path__ = [ADDON]
    sys.modules["_vb"] = shim
    spec = importlib.util.spec_from_file_location(
        "_vb.emit_preview", os.path.join(ADDON, "emit_preview.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_vb.emit_preview"] = mod
    spec.loader.exec_module(mod)
    return mod


def strip_check(path):
    """Run the real transform from emit_preview against a candidate file."""
    emit_preview = _load_emit_preview()
    original = emit_preview.PLAYER
    emit_preview.PLAYER = path
    try:
        emit_preview._player_source()
    finally:
        emit_preview.PLAYER = original


def main():
    write = "--write" in sys.argv
    if not os.path.exists(SRC):
        print(f"no player source at {SRC}")
        return 1

    same = (os.path.exists(DST)
            and open(SRC, "rb").read() == open(DST, "rb").read())

    try:
        strip_check(SRC)
    except Exception as exc:
        print(f"FAIL  the player no longer inlines cleanly:\n      {exc}")
        return 1

    if same:
        print(f"ok    add-on copy matches lerpa/src/player.js "
              f"({os.path.getsize(SRC)} bytes) and inlines cleanly")
        return 0

    if not write:
        print("STALE addon/vecbake/preview/player.js differs from lerpa/src/player.js")
        print("      run: python scripts/sync_player.py --write")
        return 1

    os.makedirs(os.path.dirname(DST), exist_ok=True)
    shutil.copy2(SRC, DST)
    print(f"copied {os.path.getsize(SRC)} bytes -> addon/vecbake/preview/player.js")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
