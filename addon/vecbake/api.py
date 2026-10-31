# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Scripting entry point.

    import vecbake
    vecbake.bake_and_write(bpy.context, "//out", "tower")

Deliberately usable without any UI so bakes can run headless
(`blender -b scene.blend -P bake.py`) as part of a build step.
"""

from __future__ import annotations

import copy
import json
import os
import re

import bpy

from . import bake as bake_module
from . import emit_lerpa, emit_json, emit_lottie, emit_preview, emit_svg
from .bake import BakeError, bake
from .settings import BakeSettings

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe(name):
    return _UNSAFE.sub("_", name).strip("_") or "camera"


def combine_stats(stats_list):
    """Aggregate per-camera stats.

    Counts add up, but `drawn_*` are per-frame peaks -- the worst case across
    cameras, not their sum, is what governs runtime cost.
    """
    combined = {}
    for stats in stats_list:
        for key, value in stats.items():
            if key.startswith("drawn"):
                combined[key] = max(combined.get(key, 0), value)
            else:
                combined[key] = combined.get(key, 0) + value
    return combined


def resolve_cameras(context, settings: BakeSettings):
    """Which cameras to bake from, in a stable order."""
    scene = context.scene
    if settings.all_cameras:
        cams = sorted((o for o in scene.objects if o.type == "CAMERA"), key=lambda o: o.name)
        if not cams:
            raise BakeError("Bake From All Cameras is on but the scene has no cameras.")
        return cams
    cam = settings.camera or scene.camera
    if cam is None:
        raise BakeError("The scene has no active camera.")
    return [cam]


def _is_clip(path):
    """Does this .svg carry an animation, or is it just a resting state?

    The container marks itself on the root tag, so the answer is in the first
    line -- no need to parse a megabyte of geometry to find out.
    """
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return 'data-lerpa="1"' in fh.read(2048)
    except OSError:
        return False


def _clips_on_disk(directory, basename, known):
    """Clips beside this bake that the index does not already account for.

    Cameras baked one at a time still add up to a folder full of clips, and
    that folder needs a manifest as much as an All Cameras bake does.
    """
    prefix = f"{basename}."
    try:
        names = os.listdir(directory)
    except OSError:
        return set()
    return {
        name for name in names
        if name.startswith(prefix) and name.endswith(".svg") and name not in known
        and _is_clip(os.path.join(directory, name))
    }


def _clip_payload(path):
    """The animation JSON carried inside a .lerpa, or None if it carries none."""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    head = text.find(f'<metadata id="{emit_lerpa.DATA_ID}"')
    if head < 0:
        return None
    start = text.find("<![CDATA[", head)
    end = text.find("</metadata>", head)
    if start < 0 or end < 0:
        return None
    body = text[start + len("<![CDATA["):end]
    if body.endswith("]]>"):
        body = body[:-3]
    # undo the split emit_lerpa uses to smuggle a literal "]]>" through CDATA
    body = body.replace("]]]]><![CDATA[>", "]]>")
    try:
        return json.loads(body)
    except ValueError:
        return None


def _clip_row(name, doc):
    """An index row rebuilt from a clip's own metadata.

    Same shape and same sources the row had when it was first written, so a
    rebuilt index is indistinguishable from an accumulated one.
    """
    meta = doc.get("meta") or {}
    return {
        "file": name,
        "camera": meta.get("camera"),
        "from": doc.get("from", meta.get("from")),
        "to": doc.get("to", meta.get("to")),
        "duration": doc.get("dur"),
        "motion": doc.get("motion"),
        "width": doc.get("w"),
        "height": doc.get("h"),
        "elements": (doc.get("stats") or {}).get("elements"),
    }


ANIMATED = ("lerpa", "json", "lottie", "svg", "preview")

# Which formats each output option actually reaches.
#
# Not every option applies to every format, and an option none of the selected
# formats consume does nothing at all. Rather than let the panel guess -- and
# drift out of step with the emitters the first time one changes -- each option
# names its consumers here, next to the code that writes the files.
#
#   bidirectional  the animated SVG reverses itself at playback, and neither a
#                  still nor a resting state has a direction to run in
#   include_ids    only the two formats carrying the element table
#   pretty         only the three JSON payloads; emit_svg has no JSON to indent
#   lerpa_states   the resting states are embedded in the animated SVG alone
CONSUMED_BY = {
    "bidirectional": frozenset({"json", "lottie"}),
    "include_ids": frozenset({"lerpa", "json", "preview"}),
    "pretty": frozenset({"lerpa", "json", "lottie"}),
    "lerpa_states": frozenset({"lerpa"}),
}

REVERSIBLE = CONSUMED_BY["bidirectional"]


def option_used(option, formats) -> bool:
    """Does any selected format consume this output option?"""
    return bool(CONSUMED_BY[option].intersection(formats))


def writes_reverse(settings, formats) -> bool:
    """Will this run actually produce `.rev` files?"""
    return bool(settings.bidirectional and option_used("bidirectional", formats))


def bake_scene(context=None, settings: BakeSettings | None = None, report=None):
    context = context or bpy.context
    return bake(context, settings or BakeSettings(), report=report)


def bake_and_write_iter(context=None, directory="//", basename="vecbake",
                        settings: BakeSettings | None = None,
                        formats=("lerpa",), rest_times=None, pretty=False):
    """Step-wise form of bake_and_write, for a UI that stays responsive.

    Yields `(phase, done, total)` and returns the same dict bake_and_write does.
    """
    result = yield from _bake_and_write(context, directory, basename, settings,
                                        formats, rest_times, pretty)
    return result


def bake_and_write(
    context=None,
    directory="//",
    basename="vecbake",
    settings: BakeSettings | None = None,
    formats=("json", "svg", "lottie"),
    rest_times=None,
    pretty=False,
    report=None,
):
    """Blocking form. See `_bake_and_write` for the work itself."""
    steps = _bake_and_write(context, directory, basename, settings, formats,
                            rest_times, pretty)
    try:
        while True:
            phase, done, total = next(steps)
            if report:
                report(phase, done, total)
    except StopIteration as finished:
        return finished.value


def _bake_and_write(
    context=None,
    directory="//",
    basename="vecbake",
    settings: BakeSettings | None = None,
    formats=("json", "svg", "lottie"),
    rest_times=None,
    pretty=False,
):
    """Bake, then serialise to every requested format.

    One bake per camera. With `bidirectional`, each bake is also written played
    backwards -- a pure transform of the keyframes, so the reverse costs no
    extra 3D work. Only for the formats in `REVERSIBLE`: everything else either
    reverses itself at playback or has no direction at all, and for those the
    flag is ignored rather than half-honoured.

    `rest_times` are the instants exported as static interactive SVG. Default
    is the first and last frame -- the two resting states a transition sits
    between. The SVGs belong to the *camera*, not the direction: a reverse bake
    runs between the same two resting states, so `<name>.rev.json` pairs with
    the same `<name>.start.svg` / `<name>.end.svg`, swapped.

    The `still` format is the odd one out: one `<name>.still.svg` of the scene
    at the current frame, with no animation in it at all. Asked for on its own
    it also shortens the bake to that single frame.
    """
    context = context or bpy.context
    settings = settings or BakeSettings()
    # No formats is a bake to measure: nothing is written, so no folder is needed.
    if formats:
        directory = bpy.path.abspath(directory)
        if not os.path.isabs(directory):
            # With an unsaved .blend, abspath turns "//out/" into a *relative*
            # "out/", which then quietly resolves against the process working
            # directory -- usually Blender's install folder. Fail loudly instead of
            # scattering files somewhere the user will never look.
            raise BakeError(
                f"Output folder '{directory}' is relative to the .blend file, but this "
                "file has not been saved yet. Save the .blend, or set an absolute "
                "output folder."
            )
        os.makedirs(directory, exist_ok=True)

    cameras = resolve_cameras(context, settings)
    multi = len(cameras) > 1
    files = {}
    outputs = []
    scene = context.scene
    # A still on its own needs one frame. Sampling the whole shot and throwing
    # all but one frame away is the slowest possible way to draw a picture, and
    # on a long clip it is the difference between a second and a minute.
    still_only = "still" in formats and not set(formats) & set(ANIMATED)
    reverse = writes_reverse(settings, formats)

    for index, cam in enumerate(cameras):
        cam_settings = copy.copy(settings)
        cam_settings.camera = cam
        if still_only:
            cam_settings.frame_start = cam_settings.frame_end = scene.frame_current
        # Always name the file after the camera, even for a single-camera bake.
        # Switching the naming on how many cameras happen to be selected means
        # baking one camera writes a different file than baking all of them,
        # so a re-bake leaves a stale copy behind instead of replacing it.
        stem = f"{basename}.{_safe(cam.name)}"

        prefix = f"{cam.name} ({index + 1}/{len(cameras)}) — " if multi else ""
        steps = bake_module.bake_iter(context or bpy.context, cam_settings)
        try:
            while True:
                phase, done, total = next(steps)
                yield (prefix + phase, done, total)
        except StopIteration as finished:
            vec = finished.value

        yield (prefix + "Writing files", 0, 1)
        variants = [("", vec)]
        if reverse:
            variants.append((".rev", vec.reversed_copy()))

        if "lerpa" in formats:
            # One self-contained asset per camera, named .svg because that is
            # exactly what it is -- every host, bundler and image viewer already
            # handles it, with no MIME configuration. Reverse needs no file of
            # its own because the player runs the timeline backwards.
            path = os.path.join(directory, f"{stem}.svg")
            files[path] = emit_lerpa.write(
                vec, path,
                include_ids=settings.include_ids,
                coord_decimals=settings.effective_coord_decimals(),
                states=settings.lerpa_states,
                name=stem,
                source=os.path.basename(bpy.data.filepath) or "",
                pretty=pretty,
            )

        for suffix, doc in variants:
            name = stem + suffix
            if "json" in formats:
                path = os.path.join(directory, f"{name}.json")
                files[path] = emit_json.write(
                    doc, path,
                    include_ids=settings.include_ids,
                    coord_decimals=settings.effective_coord_decimals(),
                    pretty=pretty,
                )
            if "lottie" in formats:
                path = os.path.join(directory, f"{name}.lottie.json")
                files[path] = emit_lottie.write(doc, path, name=name, pretty=pretty)

        # A file the animator can double-click. Written per camera, never per
        # direction: it carries a scrubber, so watching the move backwards is
        # something the page already does.
        if "preview" in formats:
            path = os.path.join(directory, f"{stem}.preview.html")
            files[path] = emit_preview.write(
                vec, path,
                name=stem,
                source=os.path.basename(bpy.data.filepath) or "",
                include_ids=settings.include_ids,
                coord_decimals=settings.effective_coord_decimals(),
            )

        # One picture of the scene as it stands, with no animation attached to
        # it -- the current frame, the way Render Image means the current frame.
        # Per camera, never per direction: a still has no direction to run in.
        #
        # Always its own single-frame bake, never a slice of the animated one.
        # Sampling the clip would tie the picture to the export range, so a
        # range that does not happen to contain the current frame would quietly
        # hand back a different frame instead. A frame costs milliseconds.
        if "still" in formats:
            yield (prefix + "Still frame", 0, 1)
            if still_only:
                shot = vec
            else:
                one = copy.copy(cam_settings)
                one.frame_start = one.frame_end = scene.frame_current
                shot = bake_module.bake(context, one)
            path = os.path.join(directory, f"{stem}.still.svg")
            files[path] = emit_svg.write(shot, path, time=0.0,
                                         decimals=settings.coord_decimals)

        # resting states are per camera, not per direction
        if "svg" in formats:
            times = rest_times if rest_times is not None else [0.0, vec.duration]
            labels = ["start", "end"] if len(times) == 2 else [str(i) for i in range(len(times))]
            for label, t in zip(labels, times):
                path = os.path.join(directory, f"{stem}.{label}.svg")
                files[path] = emit_svg.write(vec, path, time=t, decimals=settings.coord_decimals)

        outputs.append({"camera": cam.name, "basename": stem, "scene": vec,
                        "stats": vec.stats(), "reversed": reverse})

    # A filename cannot say which move a clip contains or which way it runs, and
    # guessing yields transitions that play backwards. Ship an index that
    # answers both.
    if "lerpa" in formats:
        fresh = {
            f"{out['basename']}.svg": {
                "file": f"{out['basename']}.svg",
                "camera": out["camera"],
                "from": out["scene"].meta.get("from"),
                "to": out["scene"].meta.get("to"),
                "duration": round(out["scene"].duration, 6),
                "motion": out["scene"].motion(),
                "width": out["scene"].width,
                "height": out["scene"].height,
                "elements": out["stats"]["elements"],
            }
            for out in outputs
        }
        path = os.path.join(directory, f"{basename}.index.json")
        had_index = os.path.exists(path)

        # Merge rather than overwrite. Baking one camera must update only that
        # camera's row -- rewriting the file from just this run would silently
        # drop every other clip from the index.
        clips = dict(fresh)
        if had_index:
            try:
                with open(path, encoding="utf-8") as fh:
                    for clip in json.load(fh).get("clips", []):
                        name = clip.get("file")
                        # keep only entries whose file is still on disk, so a
                        # renamed or deleted clip does not linger forever
                        if name and name not in clips and \
                                os.path.exists(os.path.join(directory, name)):
                            clips[name] = clip
            except (ValueError, OSError):
                pass                      # unreadable index: start it again

        # Clips neither this run nor the index accounts for: cameras baked one
        # at a time, or an index deleted since. Their rows come from their own
        # embedded metadata -- the same source the row had originally -- rather
        # than being guessed from the filename.
        for name in sorted(_clips_on_disk(directory, basename, set(clips))):
            doc = _clip_payload(os.path.join(directory, name))
            if doc is not None:
                clips[name] = _clip_row(name, doc)

        # The manifest answers "which clip do I play?", so it is only worth
        # writing when that is a real question. A single camera into an empty
        # folder produces one clip and no question -- just a second file to
        # explain. It appears the moment a choice exists. An index already
        # there is always kept current, never orphaned; delete it and a
        # single-clip bake will not bring it back.
        if len(clips) > 1 or had_index:
            manifest = {
                "format": "lerpa-manifest",
                "version": 1,
                "clips": [clips[k] for k in sorted(clips)],
            }
            text = json.dumps(manifest, indent=1)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            files[path] = len(text.encode("utf-8"))

    return {
        "scene": outputs[0]["scene"],
        "outputs": outputs,
        "cameras": [c.name for c in cameras],
        "bakes": len(outputs) * (2 if reverse else 1),
        "files": files,
        "stats": combine_stats([o["stats"] for o in outputs]),
    }
