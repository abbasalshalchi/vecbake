# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""A job interface for AI agents and other scripts that talk to a running Blender.

    wm = bpy.context.window_manager
    wm.vecbake.describe()                     # what is here and what can be asked for
    wm.vecbake.start({"formats": ["lerpa"]})  # returns at once; the bake runs on
    wm.vecbake.status()                       # ask as often as you like
    wm.vecbake.cancel()

Three things an agent cannot get from the operators, and why this exists:

  * A bake takes minutes and a connection into Blender has a timeout. `start`
    returns immediately and the work runs in slices off a timer, on the main
    thread, the way the panel's modal does -- never on a thread, which bpy does
    not survive.
  * An operator returns FINISHED and a line of text. These return plain dicts: the
    files written, what they weigh, what was drawn and how the camera moved, or the
    error that stopped it.
  * The add-on's module path depends on which repository installed it. A property
    on the window manager is in the same place either way.

It reaches nothing outside Blender and needs nothing else installed: whatever already
lets an agent run Python in Blender is all it asks for.

A request starts from the scene's panel settings and changes only what it names, so
an agent and a person pressing the button get the same bake from the same request.
It never writes to those settings. A name this does not know is refused, not ignored:
an agent that misspells an option should hear about it, not get a default.
"""

from __future__ import annotations

import dataclasses
import math
import os
import time
import tomllib

import bpy
from bpy.app.handlers import persistent
from bpy.types import Operator, PropertyGroup

from . import api, bake as bake_mod
from .settings import BakeSettings

API_VERSION = 1

FORMATS = {
    "lerpa": "Animated SVG: <name>.svg, one self-contained file the Lerpa player runs",
    "json": "Player JSON: <name>.json, the same animation without the SVG wrapper",
    "lottie": "Lottie: <name>.lottie.json",
    "svg": "Resting SVGs: <name>.start.svg and <name>.end.svg, the first and last frames",
    "still": "Still SVG: <name>.still.svg, the current frame with no animation",
    "preview": "Preview page: <name>.preview.html, open it in a browser to check the bake",
}

# The panel's enums, for the fields a plain string cannot be checked against.
# scripts/test_agent.py holds these to VECBAKE_PG_settings.
CHOICES = {
    "edge_mode": ("HIDDEN", "WIRE", "BOUNDARY", "SHARP", "SHADING"),
    "curve_mode": ("NONE", "SMOOTH", "ANGLE", "SHADING"),
    "stroke_source": ("FIXED", "MATERIAL"),
    "color_source": ("AUTO", "MATERIAL", "VIEWPORT"),
}

REQUEST_KEYS = {"directory", "basename", "formats", "settings", "pretty"}

_SLICE = 0.12     # seconds of work per tick, the same share of the UI the modal takes
_REST = 0.05      # seconds between ticks

_job = None


class RequestError(ValueError):
    """A request that was refused before anything ran."""


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _object_named(scene, name, field):
    obj = scene.objects.get(name) if isinstance(name, str) else None
    if obj is None:
        raise RequestError(f"settings.{field}: no object {name!r} in scene {scene.name!r}")
    return obj


def _check_setting(scene, name, value, default):
    """`value` as the bake wants it, or RequestError saying what is wrong with it."""
    if name == "objects":
        if not isinstance(value, list):
            raise RequestError("settings.objects: expected a list of object names")
        return [_object_named(scene, item, name) for item in value]
    if name == "camera":
        if value is None:
            return None
        obj = _object_named(scene, value, name)
        if obj.type != "CAMERA":
            raise RequestError(f"settings.camera: {value!r} is a {obj.type}, not a camera")
        return obj
    if name in CHOICES:
        if value not in CHOICES[name]:
            raise RequestError(f"settings.{name}: {value!r} is not one of {list(CHOICES[name])}")
        return value
    # The optional integers default to None, so their default says nothing about them.
    if name in {"frame_start", "frame_end", "width", "height"}:
        if value is None or (isinstance(value, int) and not isinstance(value, bool)):
            return value
        raise RequestError(f"settings.{name}: expected a whole number or null, got {value!r}")
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        raise RequestError(f"settings.{name}: expected true or false, got {value!r}")
    if isinstance(default, int):
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        raise RequestError(f"settings.{name}: expected a whole number, got {value!r}")
    if isinstance(default, float):
        if _number(value) and math.isfinite(value):
            return float(value)
        raise RequestError(f"settings.{name}: expected a number, got {value!r}")
    if isinstance(default, tuple):
        if isinstance(value, (list, tuple)) and len(value) == len(default) and all(map(_number, value)):
            return tuple(float(v) for v in value)
        raise RequestError(f"settings.{name}: expected {len(default)} numbers, got {value!r}")
    raise RequestError(f"settings.{name}: cannot be set from a request")


def _parse(context, request):
    """Turn a request into the arguments for a bake, or raise RequestError."""
    if not isinstance(request, dict):
        raise RequestError("the request must be a dict")
    unknown = set(request) - REQUEST_KEYS
    if unknown:
        raise RequestError(f"unknown request keys {sorted(unknown)}; expected {sorted(REQUEST_KEYS)}")

    props = context.scene.vecbake
    formats = request.get("formats", props.formats())
    if not isinstance(formats, (list, tuple)) or set(formats) - set(FORMATS):
        raise RequestError(f"formats: {formats!r} must be a list drawn from {sorted(FORMATS)}")

    settings = props.to_bake_settings(context)
    defaults = BakeSettings()
    changes = request.get("settings", {})
    if not isinstance(changes, dict):
        raise RequestError("settings must be a dict")
    known = {f.name for f in dataclasses.fields(BakeSettings)}
    unknown = set(changes) - known
    if unknown:
        raise RequestError(f"unknown settings {sorted(unknown)}; describe() lists them")
    for name, value in changes.items():
        setattr(settings, name, _check_setting(context.scene, name, value, getattr(defaults, name)))

    directory = request.get("directory", props.out_dir)
    basename = request.get("basename", props.basename)
    pretty = request.get("pretty", props.pretty)
    if not isinstance(directory, str) or not directory:
        raise RequestError("directory must be a path")
    if not isinstance(basename, str) or not basename:
        raise RequestError("basename must be a name")
    if not isinstance(pretty, bool):
        raise RequestError("pretty must be true or false")
    return {"directory": directory, "basename": basename, "settings": settings,
            "formats": list(formats), "pretty": pretty}


def _plain(value):
    """A settings value as JSON can hold it."""
    if isinstance(value, bpy.types.Object):
        return value.name
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _clip(output):
    vec = output["scene"]
    return {
        "camera": output["camera"],
        "basename": output["basename"],
        "duration": round(vec.duration, 6),
        "width": vec.width,
        "height": vec.height,
        "motion": vec.motion(),
        "stats": output["stats"],
        "could_not_merge": sorted((vec.meta or {}).get("merged_fallback") or ()),
    }


def _report(job):
    return {
        "state": job["state"],
        "phase": job["phase"],
        "done": job["done"],
        "total": job["total"],
        "elapsed_s": round((job["ended"] or time.perf_counter()) - job["started"], 3),
        "longest_block_s": round(job["longest"], 3),
        "longest_block_phase": job["longest_phase"],
        "error": job["error"],
        "cameras": job["cameras"],
        "stats": job["stats"],
        "clips": job["clips"],
        "files": job["files"],
    }


IDLE = {"state": "idle", "phase": "", "done": 0, "total": 0, "elapsed_s": 0.0,
        "longest_block_s": 0.0, "longest_block_phase": "", "error": None, "cameras": [],
        "stats": {}, "clips": [], "files": []}


def status() -> dict:
    return _report(_job) if _job else dict(IDLE)


def running() -> bool:
    return bool(_job and _job["state"] == "running")


def _props_of(job):
    try:
        return job["scene"].vecbake
    except ReferenceError:          # the scene went away under the bake
        return None


def _say(job):
    """Show the person at this Blender that a bake is running, and how far along."""
    from . import _redraw
    props = _props_of(job)
    if props is not None:
        props.progress_fraction = (job["done"] / job["total"]) if job["total"] else 0.0
        props.progress_text = f"{job['phase']} {job['done']}/{job['total']}" if job["total"] > 1 else job["phase"]
    try:
        bpy.context.workspace.status_text_set(f"VecBake: {job['phase']}")
    except AttributeError:          # no window, so no status bar
        pass
    _redraw(bpy.context)


def _close(job, state, error=None):
    from . import _redraw, _summary
    if job["steps"] is not None:
        job["steps"].close()        # unwinds the bake, restoring the frame
        job["steps"] = None
    job.update(state=state, error=error, ended=time.perf_counter())
    props = _props_of(job)
    if props is not None:
        props.baking = False
        props.progress_text = ""
        props.progress_fraction = 0.0
        if state == "done":
            props.last_stats = _summary(job["stats"], dict(job["sizes"]), job["bakes"],
                                        scenes=job["vecs"])
    # The VecScenes were only wanted for that line, and hold the whole bake in memory.
    job["vecs"] = []
    try:
        bpy.context.workspace.status_text_set(None)
    except AttributeError:
        pass
    _redraw(bpy.context)


def _finish(job, result):
    outputs = result["outputs"]
    job.update(cameras=result["cameras"], stats=result["stats"], bakes=result["bakes"],
               clips=[_clip(o) for o in outputs], vecs=[o["scene"] for o in outputs],
               sizes=result["files"],
               # From the disk, not from the emitters: they count the text before
               # Windows turns its line endings into two bytes.
               files=[{"path": path, "bytes": os.path.getsize(path)} for path in sorted(result["files"])])
    _close(job, "done")


def _note_block(job, phase, seconds):
    """Remember the longest stretch Blender could not answer, and what it was doing."""
    if seconds > job["longest"]:
        job["longest"], job["longest_phase"] = seconds, phase


def _advance(job, seconds):
    """Run the bake for about `seconds`, or to its end when None."""
    deadline = None if seconds is None else time.perf_counter() + seconds
    try:
        while True:
            began = time.perf_counter()
            running_phase = job["phase"]
            job["phase"], job["done"], job["total"] = next(job["steps"])
            now = time.perf_counter()
            _note_block(job, running_phase, now - began)
            if deadline is not None and now >= deadline:
                break
    except StopIteration as finished:
        _note_block(job, running_phase, time.perf_counter() - began)
        job["steps"] = None
        _finish(job, finished.value)
    except api.BakeError as exc:
        _close(job, "failed", str(exc))
    except Exception as exc:    # a timer that raises is unregistered and the job hangs forever
        _close(job, "failed", f"{type(exc).__name__}: {exc}")


def _tick():
    job = _job
    if job is None or job["state"] != "running":
        return None
    windows = bpy.context.window_manager.windows
    if not windows:
        _close(job, "failed", "Blender has no window left to bake in")
        return None
    try:
        # A timer has no window of its own, so it says which scene it means on each
        # tick: the one the request started in, even if the person has since moved on.
        scene = job["scene"]
        with bpy.context.temp_override(window=windows[0], scene=scene,
                                       view_layer=scene.view_layers[job["view_layer"]]):
            _advance(job, _SLICE)
            if job["state"] == "running":
                _say(job)
    except (ReferenceError, KeyError) as exc:
        _close(job, "failed", f"the scene changed under the bake: {exc}")
    return _REST if job["state"] == "running" else None


def start(request=None, wait=False) -> dict:
    """Begin a bake and return its status. Refusals come back as state "failed".

    `wait` runs it to the end before returning. A background Blender always does:
    it has no event loop, so a timer there would never fire.
    """
    global _job
    context = bpy.context
    scene = context.scene
    if running() or scene.vecbake.baking:
        return {**status(), "state": "failed", "error": "a bake is already running"}
    job = {"state": "running", "phase": "Starting", "done": 0, "total": 0, "error": None,
           "started": time.perf_counter(), "ended": None, "longest": 0.0, "longest_phase": "", "steps": None,
           "scene": scene, "view_layer": context.view_layer.name, "cameras": [], "stats": {},
           "clips": [], "files": [], "sizes": {}, "vecs": [], "bakes": 0}
    try:
        args = _parse(context, {} if request is None else request)
    except RequestError as exc:
        _job = job
        _close(job, "failed", str(exc))
        return status()
    _job = job
    job["steps"] = api.bake_and_write_iter(context, **args)
    scene.vecbake.baking = True
    if wait or bpy.app.background:
        _advance(job, None)
    else:
        _say(job)
        bpy.app.timers.register(_tick, first_interval=_REST)
    return status()


def cancel() -> dict:
    if running():
        _close(_job, "cancelled")
    return status()


def describe(context=None) -> dict:
    context = context or bpy.context
    scene = context.scene
    props = scene.vecbake
    current = props.to_bake_settings(context)
    settings = {}
    for f in dataclasses.fields(BakeSettings):
        settings[f.name] = {"value": _plain(getattr(current, f.name))}
        if f.name in CHOICES:
            settings[f.name]["choices"] = list(CHOICES[f.name])
    manifest = os.path.join(os.path.dirname(__file__), "blender_manifest.toml")
    with open(manifest, "rb") as fh:
        version = tomllib.load(fh)["version"]
    width, height = current.resolved_size(scene)
    return {
        "addon": "vecbake",
        "version": version,
        "api": API_VERSION,
        "blend": {"path": bpy.data.filepath, "saved": bool(bpy.data.filepath)},
        "scene": {
            "name": scene.name,
            "frame_start": scene.frame_start,
            "frame_end": scene.frame_end,
            "frame_current": scene.frame_current,
            "fps": scene.render.fps / scene.render.fps_base,
            "size": [width, height],
            "camera": scene.camera.name if scene.camera else None,
            "cameras": sorted(o.name for o in scene.objects if o.type == "CAMERA"),
            "drawable_objects": sum(1 for o in scene.objects if bake_mod.drawable(o)),
        },
        "request": {
            "directory": props.out_dir,
            "basename": props.basename,
            "formats": props.formats(),
            "pretty": props.pretty,
        },
        "formats": dict(FORMATS),
        "settings": settings,
        "job": status(),
    }


@persistent
def _on_load(*_args):
    """A new file takes the scene with it, so a bake cannot outlive the one it started in."""
    if running():
        _close(_job, "cancelled", "another file was opened")


class VECBAKE_PG_agent(PropertyGroup):
    """`bpy.context.window_manager.vecbake`: the same calls, wherever the add-on is installed."""

    def describe(self):
        return describe()

    def start(self, request=None, wait=False):
        return start(request, wait)

    def status(self):
        return status()

    def cancel(self):
        return cancel()


class VECBAKE_OT_agent_cancel(Operator):
    """Stop the bake a script or agent started"""

    bl_idname = "vecbake.agent_cancel"
    bl_label = "Cancel Bake"
    bl_options = {"REGISTER"}

    def execute(self, context):
        cancel()
        return {"FINISHED"}


def register():
    if _on_load not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_on_load)


def unregister():
    global _job
    if running():
        _close(_job, "cancelled", "the add-on was disabled")
    _job = None
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    if _on_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_on_load)
