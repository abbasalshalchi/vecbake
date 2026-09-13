# Audit and measurement record

Every audit and timing run behind the paint-order work of 12–13 September 2026, with
its settings and results, so a number quoted elsewhere can be traced to the run that
produced it. Newest last within each section.

How to read the numbers:

- **Wrong pixels** are ray-cast overlap pixels owned by the wrong element. Compare only
  runs of the same size (see [measurement.md](measurement.md#audit-sizes)).
- **Causes** are `audit.py`'s diagnosis of each error: `measured wrong` (the ordering
  pass read the depths wrong), `order violated` (it read them right and the cycle
  breaker gave the constraint up), `no constraint`, `occluder not drawn there`.
- **Wall-clock times on this machine are unreliable** (see
  [Timing on a loaded machine](#timing-on-a-loaded-machine)). Quote CPU time or
  back-to-back runs.

All runs are on `Documents/Blender 2026/lerpa apollo-soyuz/apollo-soyuz.blend`
(31 frames) at 1920×1080: merge fills, weld seams, SHARP edges at 30°, backface cull,
see-through, cull offscreen, occlusion cull off, accurate order, chain edges,
tol/dissolve 1 px, flat shading, fixed 1.6 px strokes. The settings are applied by
`configure()` in [`scripts/screen_surface_batch.py`](../scripts/screen_surface_batch.py).

---

## 1. Ray-cast audits

### Full audits (frames 0, 7, 15, 23, 30; 30,000 px)

| Run | Date | Errors | Wrong px | Rate | Order violated | Notes |
|---|---|---|---|---|---|---|
| Multi-ring fills skipped by ordering | 09-12 | 191 | — | — | — | "element not in this frame's ordering pass" |
| Multi-ring fills ordered (`cec7935`) | 09-12 | 176 | 1,467 | 4.89% | 75 | Baseline for the agreement experiment |
| Constraints weighted by agreement | 09-12 | 202 | 1,805 | 6.02% | **102** | Reverted; weights compress. 6,630 s total, 1,043 s bake |

### Screening audits (frames 0, 15, 30; 9,000 px)

| Run | Wrong px | Rate | Errors | Measured wrong | Order violated | `apollo_003` errors | Elements | Reported bake |
|---|---|---|---|---|---|---|---|---|
| Nearest-vertex read, nothing split | 446 | 4.96% | 93 | 53 | 37 | 43 (292 px) | 18,324 | 748 s |
| Nearest-vertex read, `apollo_003` split | 265 | 2.94% | 62 | 26 | 30 | 9 (109 px) | 18,338 | 753 s |
| **Triangle read**, nothing split | **289** | **3.21%** | 68 | **31** | 30 | 24 (170 px) | 18,324 | 1,791 s ¹ |
| Triangle read, `apollo_003` split | *pending — §5* | | | | | | | |

¹ Not a real cost of the change; see §3.

An earlier session recorded splitting the next two objects as well at 265 → 246 px for
584 more elements and "occluder not drawn there" 3 → 7. Not re-verified.

### Graph instrumentation (one bake, 31 ordering passes, nearest-vertex read)

| | |
|---|---|
| Sampled pairs | 1,320,490 |
| … with agreement < 0.80 | 16,445 (1.2%) |
| Largest stuck component at first break | 3,714 elements |
| Edges inside stuck components | 173,115, agreement < 0.80: 474 (0.3%) |
| Constraints given up | 1,496,495, agreement ≥ 0.95: 1,478,105 (98.8%) |

There is no weak class of constraint for the cycle breaker to give up first. The fault
had to be in measurement.

---

## 2. `apollo_018` under `apollo_003`, per frame

Captured ordering inputs for frames 0, 15, 30 (`out/surface-batch/capture/`, made by
`screen_surface_batch.py capture`, 169 s). Depth read at the centre of what the two
share; smaller is nearer the camera. Which rank is "in front" was fixed from the
edge-on-face assertions: `apollo_003`'s own edges are always drawn over it.

| Frame | Triangle read: thruster / body | Nearest vertex: thruster / body | Committed code draws `apollo_018` | Triangle read draws it |
|---|---|---|---|---|
| 0 | 31.21 / 31.46 | 31.21 / 32.37 | on top, 42/42 edges | on top, 42/42 |
| 15 | 32.80 / 33.03 | 32.81 / 32.97 | on top, 42/42 | on top, 42/42 |
| 30 | 34.98 / 35.24 | 34.98 / **34.25** | **under: fill and 0/41 edges** | on top, 41/41 |

Keyframes only, and exact geometry rather than the ray cast.

---

## 3. What the triangle read costs

The screening audit reported the bake 748 → 1,791 s, and that figure drove a day of
optimisation work. It was wrong.

### Ordering pass alone, replayed from the captured frames

`screen_surface_batch.py replay`, which fails if ranks or ordering stats differ.

| Run | Condition | Result |
|---|---|---|
| Unbatched vs batched surface read, 2 repeats | Normal load, both builds' frames held in one process | Ranks identical; medians 84.8 / 77.5 s, single runs from **27 s to 107 s** for the same code |
| Committed (nearest vertex), 1 repeat | Same | 22.7–26.6 s |
| `cProfile`, frame 0, `OMP_NUM_THREADS=1` | — | Triangle read 32.0 s, committed 30.9 s; `_overlap_centre` 28 s of both |
| Threads unrestricted, frame 0 | — | 33.5 s — thread count is not it |
| Frame 30, one thread | — | Triangle read 40.6 s, committed 39.0 s |

The triangle read costs the ordering pass about 4%. The 78–85 s medians were memory
pressure (2.3 GB free of 16 GB).

### Whole bakes, wall clock, worker pool

| Build | Run 1 | Run 2 (reverse order) |
|---|---|---|
| Committed | 390 s | 711 s |
| Triangle read | 657 s | 975 s |

Same code varied by 1.8×; stages the change does not touch (chaining 44 → 182 s,
sampling 180 → 428 s) swung as much as ordering did. Inconclusive by design.

### Whole bakes, CPU time, `cProfile`, one worker, frames 0–9

| | Committed | Triangle read |
|---|---|---|
| Total | 1,399 s | **1,166 s** |
| Ordering | 953 s | 866 s |
| `_overlap_centre` | 796 s | 727 s |
| `_depths_at` | 41 s (207k calls) | 16 s (80k calls, batched) |
| `_surface_tris` / `_surface_prep` / `_surface_read` | — | 0.2 / 0.2 / 0.6 s |

**The triangle read costs about 1 s per bake.** Why the audit said 2.4×: `audit.run`
forces `order_workers=1` so its diagnosis can watch the ordering pass, which makes
its reported bake 31 frames ordered serially — a measurement of machine load more
than of the code — and the two audits ran in different sessions, earlier ones in
parallel with each other.

---

## 4. Making ordering faster

Profiling showed `_overlap_centre` at 84% of ordering and 62% of the whole bake,
in code that predates the triangle read. Each change was checked by replaying the
three captured frames against a saved copy of the previous `order.py`; every row
below had **identical ranks and ordering stats**.

| Change | Ordering per frame, one thread (median of frames 0/15/30) |
|---|---|
| Reference (triangle read, batched depth reads) | 23.4 s |
| Fill pairs: one inside test per fill for the grid points of all its pairs (1.2M `_inside` calls → 80k) | 17.2 s |
| … edge crossings computed once per grid row, not per point | 10.3 s |
| Fill-vs-stroke clipping vectorised per pair | 10.1 s — overhead moved, not removed |
| … batched per fill: all segments of all its lines at once | **7.5 s** |

What remains per frame under the profiler: fill-pair inside tests 4.3 s (arithmetic),
line clipping 1.6 s, graph and cycle breaking ~2 s. No hotspot left.

### Confirmed on a bake: CPU time, no profiler, one worker, frames 0–9

| Stage | Before | After |
|---|---|---|
| Sampling | 31.8 s | 30.7 s |
| Sizing | 9.7 s | 9.5 s |
| Chaining edges | 11.7 s | 11.6 s |
| Resolving occlusion | 172.9 s | **57.4 s** |
| Reducing keyframes | 1.4 s | 1.2 s |
| **Total** | **229.6 s** | **112.2 s** |
| Elements | 9,337 | 9,337 |
| Depth + opacity track hash | `bd8ab0bc00d9b07b` | `bd8ab0bc00d9b07b` |

Committed in `20c2f71` with the triangle read.

---

## 5. Triangle read, with and without splitting `apollo_003`

Screening audit, 13 September, `20c2f71`, run one arm at a time with nothing else of
ours on the machine. `split_span_pct = 0` in both; the split arm forces `apollo_003`
out of merging through the harness (`screen_surface_batch.py audit --arm split`).

| | Triangle read, nothing split | Triangle read, `apollo_003` split |
|---|---|---|
| Wrong pixels | 289 | **214** |
| Rate | 3.21% | **2.38%** |
| Errors | 68 | **41** |
| Measured wrong | 31 | **15** |
| Order violated | 30 | **20** |
| Occluder not drawn there | 3 | 3 |
| No constraint | 2 | 1 |
| Coverage edge | 2 | 2 |
| Errors involving `apollo_003` | 24 (170 px) | **7 (109 px)** |
| Elements | 18,324 | 18,338 (+14) |
| Bake (serial ordering) | 406 s | 423 s |
| Whole audit | 928 s | 713 s |

Wrong pixels between `apollo_003` and each part, by object:

| Part | Nothing split | Split |
|---|---|---|
| `apollo_002` | 97 | 80 |
| `apollo_013` | 39 | 19 |
| `apollo_024` | 24 | 6 |
| `apollo_018` (all pieces) | 10 | 4 |

The nothing-split arm reproduces §1 exactly (289 px, 68 errors, 31 / 30), so the audit
is deterministic and the ordering speed-up changed nothing it can see. The whole audit
now takes 12–16 minutes, not 40: the ray cast of 9,000 pixels takes about two seconds,
and the time was the serial bake and the diagnosis.

**Reading.** Splitting still earns its place against the triangle read: a quarter of
the wrong pixels and 40% of the errors gone, for 14 elements and ~4% more bake. What
it fixes is no longer the depth read. A merged body has one rank per frame, and a part
that sits in front of one face of it and behind another — `apollo_002` wraps the
module — cannot be right with one rank whatever depth is read. That is granularity.

**So `split_span_pct` is not removed — but its selector is not justified either.** The
50% threshold has no measured knee (58.2 / 51.7 / 42.6% on this model). Which bodies
to draw face by face needs a criterion that is not a tuned share. Open.

---

## Timing on a loaded machine

This machine runs Chrome, Voicemeeter and the Blender GUI; CPU sat at 53–56% before
bakes started and free memory at 2–4 GB. Under that load the same bake timed 390 and
711 s, and the same ordering replay 27 and 107 s. Rules that came out of it:

- Compare **CPU time** (`time.process_time()`, one worker so the ordering pass runs
  in-process), or back-to-back runs in alternating order.
- An audit's "bake seconds" is serial ordering; never read it as bake cost.
- Run audits one at a time.
- A replay that holds two copies of every frame in one process measures memory.
