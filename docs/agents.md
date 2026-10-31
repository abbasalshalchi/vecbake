# VecBake for AI agents

VecBake turns a camera move in a Blender scene into a 2D vector animation. An agent that can
run Python in Blender can drive it through four calls. Nothing else needs installing: no extra
add-on, no server of VecBake's own, no network.

Everything lives on the window manager, wherever the add-on was installed from:

```python
import bpy
vb = bpy.context.window_manager.vecbake
```

If that attribute is missing, the add-on is not enabled.

## The usual run

```python
# 1. See what is here: the scene, its cameras, the formats, every setting and its value.
result = vb.describe()

# 2. Optional: measure without writing. No formats means no files and no folder.
vb.start({"formats": []})

# 3. Bake. Returns at once with state "running"; the bake goes on while Blender stays usable.
result = vb.start({
    "directory": "C:/work/out",      # "//out" works too, once the .blend is saved
    "basename": "hero",
    "formats": ["lerpa", "preview"],
})

# 4. Ask until the state changes, in separate calls.
result = vb.status()
```

Then look at the result: open the `preview` file in a browser, or render it. `status()` says what
was drawn. Whether it looks right is a thing only eyes on the output can tell.

To wait inside one call on the Blender Lab MCP server, define `check_is_finished`. The server polls
it and answers when it stops returning `None`:

```python
vb.start({"directory": "C:/work/out", "basename": "hero", "formats": ["lerpa"]})

def check_is_finished():
    s = vb.status()
    return None if s["state"] == "running" else s
```

## Calls

| Call | Returns |
|---|---|
| `describe()` | Version, the .blend, the scene, formats, every setting with its current value and choices, the job |
| `start(request=None, wait=False)` | The job's status. `wait=True` runs to the end first |
| `status()` | The job's status |
| `cancel()` | The job's status, with the frame put back |

All return plain dicts. None of them raise on a bad request: a refusal is `state: "failed"` with
the reason in `error`.

## The request

Every key is optional. Anything left out comes from the scene's VecBake panel, so a request with
nothing in it bakes what pressing **Bake & Export** would bake, byte for byte
(`scripts/test_agent.py` holds it to that). A request never changes the panel.

| Key | Meaning |
|---|---|
| `directory` | Where files go. `//` is relative to the .blend, and fails if it is unsaved |
| `basename` | Files are named `<basename>.<camera>.<format extension>` |
| `formats` | A list from the table below. Empty means measure only |
| `settings` | Any setting `describe()` lists, by name |
| `pretty` | Indent the JSON |

Unknown keys, settings and formats are refused rather than ignored. In `settings`, `objects` and
`camera` take names. Colours are three scene-linear numbers, and angles are degrees.

| Format | Writes |
|---|---|
| `lerpa` | `<name>.svg`, one self-contained animated file for the Lerpa player |
| `json` | `<name>.json`, the same animation without the SVG wrapper |
| `lottie` | `<name>.lottie.json` |
| `svg` | `<name>.start.svg` and `<name>.end.svg`, the first and last frames |
| `still` | `<name>.still.svg`, the current frame with no animation |
| `preview` | `<name>.preview.html`, a page to check the bake in |

## The status

| Key | Meaning |
|---|---|
| `state` | `idle`, `running`, `done`, `failed` or `cancelled` |
| `phase`, `done`, `total` | Where the bake has got to |
| `elapsed_s` | Time since `start` |
| `longest_block_s`, `longest_block_phase` | The longest Blender could not answer, and in which phase |
| `error` | What stopped it, or `null` |
| `files` | Each file's `path` and `bytes` as they are on disk |
| `stats` | Elements, strokes, fills, keyframes, peak drawn per frame |
| `clips` | Per camera: duration, size, `motion`, stats, and any object that `could_not_merge` |
| `cameras` | The cameras baked |

## What to know

- **One bake at a time.** A second `start` is refused and the first carries on.
- **The frame moves during a bake** and comes back at the end, on cancel, and on failure. Someone
  at the machine sees the panel's progress and a **Cancel Bake** button.
- **Opening another file cancels the bake**, because the scene it was baking has gone.
- **A camera that does not move** still bakes. Check `motion`.
- **A background Blender** (`blender -b`) has no event loop, so `start` there always runs to the end
  before it returns.
- **The bake runs in slices on Blender's main thread**, so other calls wait for the current slice.
  Usually that is under two seconds. On a 16,000-element scene, the longest was 15 s.
