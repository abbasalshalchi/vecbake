"""When the clip index is written, and when it is not.

    "<blender>/<version>/python/bin/python.exe" scripts/test_index_written.py

The rule under test: an index answers "which clip do I play?", so it exists
exactly when that is a real question. Exercises the decision against files on
disk rather than a mocked filesystem, because half of it *is* filesystem
behaviour -- telling a clip from a resting state, and finding clips left by an
earlier bake.
"""

import json
import os
import shutil
import tempfile

import _addon

# api.py imports Blender for the parts that bake; the four helpers below
# only read and write files, so a stub is enough to get at them.
_addon.stub_blender()
_api = _addon.load("api")
_clip_payload = _api._clip_payload
_clip_row = _api._clip_row
_clips_on_disk = _api._clips_on_disk
_is_clip = _api._is_clip

PAYLOAD = {"w": 800, "h": 600, "dur": 0.5, "motion": {"scale": 0.4, "direction": "out"},
           "stats": {"elements": 12}, "meta": {"camera": "a cam", "from": "x", "to": "y"}}
CLIP = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8" data-lerpa="1">\n'
        '<metadata id="lerpa-data" data-format="lerpa"><![CDATA['
        + json.dumps(PAYLOAD) + ']]></metadata>\n</svg>')
STATE = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8">\n<rect width="8" height="8"/>\n</svg>'


def _resolve(directory, basename, clips):
    """The decision api._bake_and_write makes, kept in step by hand.

    Returns the rows it would write, or None for "write no index".
    """
    path = os.path.join(directory, f"{basename}.index.json")
    had_index = os.path.exists(path)
    clips = dict(clips)
    for name in sorted(_clips_on_disk(directory, basename, set(clips))):
        doc = _clip_payload(os.path.join(directory, name))
        if doc is not None:
            clips[name] = _clip_row(name, doc)
    return clips if (len(clips) > 1 or had_index) else None


def _wanted(directory, basename, clips):
    return _resolve(directory, basename, clips) is not None


def _write(directory, name, body):
    with open(os.path.join(directory, name), "w", encoding="utf-8") as fh:
        fh.write(body)


def test_single_clip_alone_writes_nothing():
    d = tempfile.mkdtemp()
    try:
        _write(d, "transition.Camera.svg", CLIP)
        assert not _wanted(d, "transition", {"transition.Camera.svg": {}})
    finally:
        shutil.rmtree(d)


def test_resting_states_do_not_count_as_clips():
    """`.start.svg` shares the prefix but carries no animation."""
    d = tempfile.mkdtemp()
    try:
        _write(d, "transition.Camera.svg", CLIP)
        _write(d, "transition.Camera.start.svg", STATE)
        _write(d, "transition.Camera.end.svg", STATE)
        assert not _wanted(d, "transition", {"transition.Camera.svg": {}}), \
            "resting states are not a choice of clip"
    finally:
        shutil.rmtree(d)


def test_two_cameras_in_one_bake_write_it():
    d = tempfile.mkdtemp()
    try:
        clips = {"t.a_cam.svg": {}, "t.b_cam.svg": {}}
        assert _wanted(d, "t", clips)
    finally:
        shutil.rmtree(d)


def test_second_camera_baked_separately_lists_both():
    """One at a time still adds up to a folder needing a manifest.

    And the manifest has to name the clip this run did not touch, or the very
    lookup it exists for cannot find it.
    """
    d = tempfile.mkdtemp()
    try:
        _write(d, "t.a_cam.svg", CLIP)          # from an earlier bake
        _write(d, "t.b_cam.svg", CLIP)          # this one
        rows = _resolve(d, "t", {"t.b_cam.svg": {"file": "t.b_cam.svg"}})
        assert rows is not None, "two clips need an index"
        assert set(rows) == {"t.a_cam.svg", "t.b_cam.svg"}, \
            f"the earlier clip must be listed too, got {sorted(rows)}"
        recovered = rows["t.a_cam.svg"]
        assert recovered["camera"] == "a cam" and recovered["duration"] == 0.5 \
            and recovered["elements"] == 12 and recovered["motion"]["direction"] == "out", \
            f"row rebuilt from the clip's own metadata, got {recovered}"
    finally:
        shutil.rmtree(d)


def test_cdata_escape_survives_the_round_trip():
    """emit_lerpa splits a literal "]]>" across two sections; undo it exactly."""
    emit_lerpa = _addon.load("emit_lerpa")
    payload = dict(PAYLOAD, meta={"camera": "]]> odd"})
    text = ('<svg data-lerpa="1"><metadata id="lerpa-data">'
            + emit_lerpa._cdata(json.dumps(payload)) + "</metadata></svg>")
    d = tempfile.mkdtemp()
    try:
        _write(d, "t.weird.svg", text)
        doc = _clip_payload(os.path.join(d, "t.weird.svg"))
        assert doc is not None and doc["meta"]["camera"] == "]]> odd", doc
    finally:
        shutil.rmtree(d)


def test_existing_index_is_kept_current():
    d = tempfile.mkdtemp()
    try:
        _write(d, "t.a_cam.svg", CLIP)
        with open(os.path.join(d, "t.index.json"), "w", encoding="utf-8") as fh:
            json.dump({"format": "lerpa-manifest", "version": 1, "clips": []}, fh)
        assert _wanted(d, "t", {"t.a_cam.svg": {}}), "never orphan an index"
    finally:
        shutil.rmtree(d)


def test_other_basename_is_not_ours():
    d = tempfile.mkdtemp()
    try:
        _write(d, "other.a_cam.svg", CLIP)
        assert not _wanted(d, "t", {"t.b_cam.svg": {}})
    finally:
        shutil.rmtree(d)


def test_is_clip_reads_only_the_head():
    d = tempfile.mkdtemp()
    try:
        _write(d, "big.svg", CLIP.replace("</svg>", "<!--" + "x" * 500000 + "--></svg>"))
        assert _is_clip(os.path.join(d, "big.svg"))
        assert not _is_clip(os.path.join(d, "missing.svg"))
    finally:
        shutil.rmtree(d)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print("ok  ", fn.__name__)
    print(f"\n{len(tests)} passed")


main()
