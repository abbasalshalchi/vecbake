"""Load add-on modules from a plain Python, for tests that need no Blender.

`from vecbake import x` works in neither place a test actually runs. Outside
Blender there is no `vecbake` on the path; inside it, an installed add-on is an
extension and answers to `bl_ext.user_default.vecbake`. Several test scripts
carried the first form and so could not run at all -- and one of them was the
occlusion gate the README points at.

Loading by path sidesteps both, and lets a test of pure arithmetic run in a
second under the bundled interpreter instead of needing a Blender to start:

    "<blender>/<version>/python/bin/python.exe" scripts/test_thing.py
"""

import importlib.util
import os
import sys
import types

ADDON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "addon", "vecbake")


def stub_blender():
    """Satisfy `import bpy` for modules that import it but do not call it.

    Only for code whose tested part is arithmetic or file handling. Anything
    that actually reaches into Blender belongs in a test that runs there --
    see test_silhouette_objects.py, which does.
    """
    for name in ("bpy", "mathutils"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["mathutils"].Vector = object
    sys.modules["mathutils"].Matrix = object


def load(name):
    """Import one add-on module by path, with its package-relative imports intact."""
    if "_vb" not in sys.modules:
        pkg = types.ModuleType("_vb")
        pkg.__path__ = [ADDON]
        sys.modules["_vb"] = pkg
    already = sys.modules.get(f"_vb.{name}")
    if already is not None:
        return already
    spec = importlib.util.spec_from_file_location(
        f"_vb.{name}", os.path.join(ADDON, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"_vb.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod
