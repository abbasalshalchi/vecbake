# Measuring without being fooled

Most of the wrong turns in this project were not bad ideas. They were good ideas
graded by a number that did not measure what it appeared to. This page is the
tools that grade a bake, and every trap that has produced a confident wrong
answer.

The rule underneath all of it: **a number is a claim until it has been checked
against ground truth, or against a deliberately broken version of the fix.** If
a change moves the metric and not the artefact, the metric is wrong.

---

## The tools

| Tool | What it grades | Ground truth? | Run |
|---|---|---|---|
| `addon/vecbake/audit.py` | Paint order: ray-casts overlapping pixels against the real mesh and diagnoses each error | **Yes** | Panel: Audit → Check Occlusion. Headless: `audit.run(frames=[...], budget=N)` |
| `scripts/check_geometry.py` | Position: every drawn element's points against the projection of its own mesh vertices, exhaustively, gated on opacity | **Yes** | `exec(open('.../check_geometry.py').read()); run('.../x.svg')` inside Blender |
| `scripts/validate_bake.py` | The emitted order against the ordering pass's own sampled evidence; `lines_under_own_face`; `order_flips_after_reduction` | No — self-consistency | `check()` in `scripts/dev.py` |
| `scripts/check_occlusion.py` | Thin shim over `audit.py` for headless runs | Yes | — |
| `scripts/screen_surface_batch.py` | `capture`: pickle the ordering pass's input for chosen frames. `replay`: run two `order.py` files on captured frames and **fail if any rank or stat differs**, with timings. `audit`: the Apollo screening audit, `--arm triangle` (nothing split) or `--arm split` (`apollo_003` face by face), writing `summary.json` | Replay: exact equivalence. Audit: yes | `blender -b apollo-soyuz.blend --python scripts/screen_surface_batch.py -- audit --arm split --out out/…`; replay runs in the bundled Python, no Blender |
| `scripts/check_outline_segments.py` | Merged-outline and chain **segments** at ¼, ½, ¾ against the object's projected edges — `check_geometry` grades points only, and a ring joining right points in the wrong order passes it | Yes | `blender -b scene.blend --python scripts/check_outline_segments.py -- <bake.preview.html>` |
| `scripts/test_interleave.py`, `interleave_lab.py`, `check_interleave_evidence.py` | The interleaving split selector on known fixtures (`--legacy` must fail); Blender ray casts of its saved witnesses | Lab: yes; witnesses: yes | See [audits.md §7](audits.md#7-interleaving-selector-first-attempt) |
| `scripts/geometry_signature.py` | What is in a .blend: per-object mesh hash, vertex and face counts, transform, modifiers, and whether anything is left unapplied. Reads geometry-node inputs from both their 5.1 and 5.2 homes | Exact | `blender -b scene.blend --python scripts/geometry_signature.py -- --out sig.json`; `--compare before.json after.json` |
| `scripts/measure_subframe.py` | A merged outline against Blender's true silhouette at subframes | **Yes** | `T_TAG`, `T_NAMES` env vars |
| `scripts/fit_orbit_depth.py` | Per-node camera distance `d² = A + B cos θ + C sin θ` for an orbit or an arc | Exact for a rigid orbit | `arc_degrees` for an arc |
| Bake stats | `order_constraints`, `order_cycle_breaks`, `order_constraints_dropped`, `order_assertions_lost`, `merged_gap_*`, `shading_angle_guessed` | Diagnostics | In every bake's report |
| `tools/play.html` | Loads the player from disk, so a player change can be tested without re-baking | — | `localhost:8765/tools/play.html?f=<clip>` |
| `scripts/devserver.py` | Serves the repo, falling back to `lerpa-website/public`; `POST /save/<name>` writes canvas snapshots to `snapshots/` | — | Port 8765 |
| `lerpa-website/public/_lab.html` | Rasterises one group's fills with the shipping renderer | — | `?src=` |

Grade occlusion by the audit, position by `check_geometry`, outline shape by the
subframe probe. Read `validate_bake` as "how far the order strays from its own
evidence", not as correctness. Before blaming a
code change for a difference between two bakes, run `geometry_signature` on both
.blend files and rule the scene out.

### Two traps found rebuilding the nick metric, 2026-10-10

The metric behind "visible nicks" in [audits.md](audits.md) sections 16-18 was lost
with its scratch directory. Rebuilding it failed twice, and both failures are worth
keeping because either would have produced a confident number.

**An element's `depth` is not a depth.** `ir.py` called it "camera-space distance",
and it starts as one, but what is emitted is the rank `order.resolve` returned --
a position in a total order (`bake.py`, "Resolving occlusion"). Comparing it with a
ray cast is meaningless. The real per-pixel depths are in the items handed to
`order.resolve`, reachable by spying on it the way `audit.run` does and reading
them with `order._depths_at`. Those *do* agree with `_Truth`: on a captured Apollo
frame, every line whose own object the ray hits matches to +-0.000 units. The
comment is corrected.

**`_Truth.surface_of` answers a different question.** It returns an object's
nearest surface, which is what the audit wants for "was this pixel painted with
the wrong thing". For "was this line visible", it reads every line on the far side
of its own body -- correctly hidden by its own skin -- as visible. That version
reported 409,788 nicks on the Apollo clip where the figure is in the low thousands.

**And the reason the rebuild is still not committed.** Both halves of the measure
rest on `audit._paint`, which is not a model of what the player draws:

- `_element_mask` pads a stroke to `width/2 + 0.35` -- 2.3 px for a 1.6 px stroke --
  and `top` is built from the same padded masks, so neighbouring elements claim each
  other's fringes and short edges come back 100% covered.
- `_paint` draws fills as polygons only. The player also strokes **every fill in its
  own colour at one device pixel or more**, to close the cracks between abutting
  faces. That hairline stroke is the mechanism that nicks a line. A nick metric built
  on this painter cannot see the thing it exists to measure.

So a faithful nick metric needs a painter that draws what the player draws, at the
widths it draws them. Until there is one, grade lines by the audit, which asks a
question `_paint` can answer, and treat section 16-18's nick figures as a record of
runs that can no longer be repeated.

### Keep the tool, not just the number

The measurements in [audits.md](audits.md) sections 15-18 were taken with scripts
written in a session scratch directory, which was cleaned. The numbers survived in
these documents and the tools did not, so none of those runs could be repeated --
and a number nobody can repeat is a claim, which is the one thing this page exists
to prevent. `measure_line_truth.py` and `geometry_signature.py` are rebuilds of
two of them, written on 2026-10-10 against the behaviour the sections describe.
A measurement worth writing down is worth committing the tool for, in `scripts/`,
the same day.

### Audit sizes

A full audit costs far more than the bake it grades — ray-casting dominates.

| | Frames | Overlap pixels | Time on Apollo–Soyuz |
|---|---|---|---|
| Full | 0, 7, 15, 23, 30 | 30,000 | ~110 min (bake ~17 min) |
| Screening | 0, 15, 30 | 9,000 | ~40 min before `20c2f71`; 12–16 min after |

Compare a screening run only with a screening run. Screen first; confirm the winner
at full size.

The ray cast is not the cost: 9,000 pixels take about two seconds. An audit's time is
its bake, which orders every frame on one worker so the diagnosis can watch, and the
diagnosis of each error. Run audits one at a time.

---

## Traps that produced wrong answers

### Grading between keyframes, not at them
An interpolated format is exact at every keyframe by construction, so every rule
looks equally good there. part_042 measured 0.05 px at keyframes and 14.26 px
between them. **Measure at `subframe = 0.5`.** The same mistake sized point counts
wrong for months, and sent the hero's framing work round three times: anything that
samples at frame positions and re-interpolates disagrees with what is drawn between.

### A metric that moves while the artefact does not
Nine changes to merged outlines were made against proxies — area dips, slot
displacement, winding changes — that ranked parts differently from what a person
saw. The subframe probe against Blender's own silhouette is what ended it
(`e865e00`).

### `occlusion_violations` and the ray cast can move in opposite directions
When merged fills were ordered against their real surface, ray-cast wrong pixels
halved (4.52% → 2.24%) while `occlusion_violations` nearly doubled (5,799 → 10,474),
85% of the increase on pairs with a merged element. The validator grades by the mean
depth over 24 samples; `resolve` decides at one point. They agreed only while depth
was a plane.

### Fewer constraints dropped is not better
Agreement weighting dropped fewer constraints (281 → 257 on a synthetic crowd) and
was worse on the real model: fewer, but chosen badly. Grade on the ray cast.

### The audit has been wrong
- It sampled reduced tracks at `fi/fps`, ignoring frame step: 1.7M phantom order
  flips.
- It graded on-face pairs by the same plane fit the baker overrides.
- A between-keyframes scan sampled past `duration`: the whole scene ~47 px wrong at
  once. **The tell was every part sharing one timestamp.**
- Its per-pair rectangle survey was quadratic and never finished on a real scene.
- Its diagnosis spied on an in-process call while ordering ran in workers.

### A validator that passes without running
`validate_bake.py` could not import on a real install and, had it imported, graded
an empty list as PASS. Anything that works by spying on a function must **raise** if
the spy never fired.

### Tests that assert the mechanism instead of the fault
- `test_edge_shading.py` used 30°, the same as the fallback, so it passed with the
  bug live.
- `test_encode_grid.py` built metadata by hand and so only tested the consumer.
- A `freeze_hidden` test fed real-but-distant poses where the real fault was `None`.
- Every shape in `test_outline.py` was convex, so self-crossing outlines passed.

Before trusting a test, run it against the commit that had the bug.

### Sampling walks past small faults
A stray line is a few dozen pixels in a few hundred thousand: 7,000 random samples
over a bake with four known errors found none. Restrict to where the fault can
exist — overlaps for ordering — or be exhaustive, as `check_geometry` is.

### Forgetting the opacity gate
Without gating on opacity > 1/255, `check_geometry` reports 57,402 offenders on the
Voyager hero, every one correctly invisible.

### Never score pixels off a canvas in the page
Chrome composites an in-document canvas, and it does not settle to the same raster
twice: 2,952 differing pixels where the true residual was 9, with the player's
internal state bit-identical. No redraw, `requestAnimationFrame` or delay fixed it.
Measure at the document's own size with no transform change (`out/usr_weld.html`).

### `lastRenderMs` is an order of magnitude optimistic
Canvas2D submits asynchronously. Force a finish — read back one pixel
(`getImageData` / `readPixels`) — before timing a frame.

### A hidden tab does not render
`requestAnimationFrame` does not run in a hidden tab, and a screenshot times out.
The in-app browser pane can report `document.hidden: true` even when fronted. Check
`document.visibilityState` before believing a frame rate, or a freeze.

### Comparing two bakes of two different scenes
An old-vs-new comparison on the Voyager was confounded because the `.blend` was
re-saved between the bakes. Record the scene state with the numbers.

### Machine noise
Two runs producing byte-identical output timed 3.64 and 2.11 minutes. Trust element
counts and pixel counts; quote bake times only against a back-to-back run.

On 13 September the same Apollo bake timed 390 and 711 s, and a change was reported
as making the bake 2.4× slower when CPU profiling put its cost at about one second.
Time with `time.process_time()` on one worker, and never read an audit's bake time
as bake cost: `audit.run` orders every frame serially. Runs in
[audits.md](audits.md#3-what-the-triangle-read-costs).

### Waiting on output that was never going to match
A wait loop grepping for `Error:` exited immediately on Blender's harmless
`ModuleNotFoundError: No module named 'imp'` startup line, reporting a 40-minute
audit as finished. Wait on the specific result line, or on the process exiting.

### A benchmark against a weakened baseline
Cubic time interpolation promised 40% off strokes against top-down linear fitting;
the reducer that ships uses RDP, and against that it was 10% off geometry. Benchmark
against what actually ships.

### Degrees into a radians property
A crease-angle sweep passed 30.0 to an `ANGLE` property, stored in radians and capped
at π: every row came out identical. Sanity-check a sweep's control row against a known
bake.

### Ground truth from a scene you control
The strays that took longest were isolated by a scene of simple primitives on the
same camera and settings, not by the 300-part model. When a real model is too big to
reason about, build the smallest scene whose right answer you can state in advance —
`boss` (a box on a plate) is what made the multi-ring winding test a test.

---

## Timing reference

| Measurement | Value |
|---|---|
| Voyager bake before workers / after | 66.9 / 8.9 min |
| Voyager 60-frame bake at 15.7 MB | 12.5 min |
| Apollo–Soyuz 3-frame screening bake | ~750 s (serial ordering; load-dependent) |
| Apollo–Soyuz 10-frame bake, one worker, CPU | 229.6 s → 112.2 s after `20c2f71` |
| Apollo–Soyuz full audit (5 frames, 30k px) | 6,630 s total, 1,043 s bake |
| Lerpa frame at 960 px, 6,307 elements | ~52 ms (JS 17, paths 6–12, raster 24–29) |
| three.js, same model, 960 / 1080 | 64–72 / 55–61 fps |
| Hairline cliff | ~5× slower above 1.0 device px stroke |
