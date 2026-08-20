"""Dev helpers: load the add-on from this folder without installing it.

Paste into Blender's Python console (or the MCP bridge):

    exec(open(r'.../scripts/dev.py').read())
    reload()                 # re-import after editing the add-on
    bake()                   # bake + write to out/
    check()                  # audit the bake for occlusion errors
    audit()                  # ray-cast every frame and list what is wrong
    install()                # copy into Blender's extensions folder
    scene(confirm=True)      # DESTRUCTIVE: wipes the scene for the test tower

`reload()` drops the package from sys.modules first, so edits land without
restarting Blender.
"""

import os
import runpy
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(ROOT, "addon")
OUT = os.path.join(ROOT, "out")


def reload():
    if ADDON not in sys.path:
        sys.path.append(ADDON)
    for name in [k for k in list(sys.modules) if k == "vecbake" or k.startswith("vecbake.")]:
        del sys.modules[name]
    import vecbake
    print("vecbake reloaded from", ADDON)
    return vecbake


def scene(confirm=False):
    """Rebuild the test tower. DESTRUCTIVE -- wipes every object in the scene.

    Guarded, because it is one keystroke away from the harmless-looking
    helpers and would take a real project with it.
    """
    existing = [o.name for o in bpy.context.scene.objects]
    if not confirm:
        raise RuntimeError(
            f"scene() deletes all {len(existing)} objects in the current scene "
            f"({', '.join(existing[:6])}{'...' if len(existing) > 6 else ''}). "
            "Call scene(confirm=True) if that is really what you want."
        )
    ns = runpy.run_path(os.path.join(ROOT, "scripts", "build_test_scene.py"))
    counts = ns["build_scene"]()
    total = sum(c["edges"] + c["faces"] for c in counts.values())
    print(f"test scene rebuilt: {total} elements before culling")
    return counts


def bake(basename="tower", **kwargs):
    vecbake = reload()
    from vecbake.settings import BakeSettings
    settings = BakeSettings()
    for key, value in kwargs.items():
        setattr(settings, key, value)
    result = vecbake.bake_and_write(bpy.context, directory=OUT, basename=basename,
                                    settings=settings)
    print(result["stats"])
    for path, size in result["files"].items():
        print(f"  {os.path.basename(path):<24} {size / 1024:7.1f} KB")
    return result


def check(**kwargs):
    reload()
    from vecbake.settings import BakeSettings
    settings = BakeSettings()
    for key, value in kwargs.items():
        setattr(settings, key, value)
    ns = runpy.run_path(os.path.join(ROOT, "scripts", "validate_bake.py"))
    return ns["validate"](settings)


def audit(**kwargs):
    """Ray-cast the bake against the 3D scene and list every occlusion error.

    Uses the panel's own settings unless overridden. See check_occlusion.py --
    it reports what is wrong, where, and which of the three causes it is, so
    the ones that are modelling rather than baker bugs say which face to split.

        audit()                    # every frame
        audit(frames=[0, 24])      # a couple
        audit(budget=3000)         # more ray casts per frame
    """
    reload()
    ns = runpy.run_path(os.path.join(ROOT, "scripts", "check_occlusion.py"))
    return ns["run"](**kwargs)


def install():
    """Copy the add-on into Blender's user extensions folder."""
    import shutil
    repos = {r.module: r.directory for r in bpy.context.preferences.extensions.repos}
    target_root = repos.get("user_default")
    if not target_root:
        raise RuntimeError("no user_default extension repository")
    target = os.path.join(target_root, "vecbake")
    if os.path.isdir(target):
        shutil.rmtree(target)
    shutil.copytree(os.path.join(ADDON, "vecbake"), target,
                    ignore=shutil.ignore_patterns("__pycache__"))
    print("installed to", target)
    print("enable it in Edit > Preferences > Add-ons (search 'VecBake')")
    return target


print(__doc__)
