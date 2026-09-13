# Invariants

Properties of the bake and the player that took real debugging to establish.
Each one has produced a visible artefact that looked like a rendering bug and was
an ordering, encoding or correspondence bug. Before changing code near one of these,
read the entry, then read the comment where it lives.

The README's [The three things that were hard](../README.md#the-three-things-that-were-hard)
explains the design; this page is the list of things not to break.

---

## Interpolation and correspondence

**Grade between keyframes.** What a viewer sees is the shape between two keyframes.
A choice that is optimal at every keyframe, and differently optimal at each, is worse
than one that stays put. Placement, point counts and outline judgement are all
measured at `subframe = 0.5`. Optimising a merged outline's placement harder made it
monotonically worse (22.47 → 30.20 px on part_079). → [experiments.md](experiments.md#3-point-placement-and-sizing)

**Identity is a mesh index.** Edge 237 is edge 237 in every frame, which is what makes
correspondence free. It requires topologically static geometry; `plan.check` refuses a
mesh that changes. Mesh indices restart at zero per object, so an index is only
meaningful beside its object (`78a1b8e`).

**An element's point count is fixed for the clip.** A merged outline's slots are
positions around the loop, not mesh vertices, kept monotone so they cannot cross
(`e6be3af`). Never more points than the ring has vertices plus two (`7377876`).

**A merged outline keeps one winding.** A ring that flips direction between frames
interpolates through its mirror image and collapses to a line between keyframes
(`6ff5f67`).

**A part merges for the whole clip or not at all.** Nothing changes representation
mid-animation (`5fae344`, `e5b0365`).

---

## Paint order

**Order a pair at the centre of what it shares.** One point, continuous as the overlap
moves: the centroid of the shared area, or the midpoint of the shared length. A mean
over a sample set steps when membership changes, and pairs trade places on the step
(`9b58e0e`).

**Sampling follows pixels, and clips exactly where it must.** A fixed count per pair
stepped over a 41 px dish on a 666 px leg, and over a 3.8 px edge-on face. Line against
fill is solved by segment/polygon intersection (`68585a4`); fill against fill by a
pixel-scaled grid with an outline walk for slivers (`069d762`).

**Walk the closing segment of a closed chain.** `closePath()` draws one segment more
than the point list (`46d0446`).

**An edge lies on its faces; that is asserted, not measured.** A fill must never paint
over its own outline. Retract any opposing measured edge first, or the pair is a
2-cycle. `lines_under_own_face` must be 0.

**Assertions are spent last, and never to fix a still frame.** Spending them first
alternates stroke width in motion (`38fb353`). Recording an assertion where sampling
already agreed is what keeps it out of the breaker's reach (`3f9409d`).

**Cycle breaking is surgical.** Only edges inside a strongly connected component; the
weakest tenth *by count*, not by threshold; every constraint created with a strength —
disjoint depth ranges are the most certain edges in the graph, not the least.

**Weights stay spread out.** Constraints are ranked by depth gap. Scoring by agreement
compressed every weight into a narrow band and made the breaker's choice arbitrary
(2026-09-12).

**Never skip pairs with disjoint depth ranges.** The topological sort reorders
globally, so seed order does not survive for unconstrained pairs.

**A merged part is measured against its surface, not its rim.** `dmin`/`dmax` come
from the probe, and the probe's thinning keeps the nearest and furthest vertices —
`resolve` settles non-overlapping ranges without measuring (`6274add`).

**Emitted depth is a rank, strictly injective.** Edges inherit their face's seed depth,
so seeds tie.

**Culled frames stay on the rank scale.** Filled from the nearest ranked frame in both
directions, or hidden geometry dives to the front across one frame (`2687985`).

**`resolve` is pure.** Its only unordered containers are sets of small integers, so
worker count cannot change a rank. Holding element keys in those sets would break
reproducibility silently. → `test_parallel.py`

---

## Keyframes and encoding

**Depth reduction uses a per-sample budget** — half the gap to the nearest element in
depth. A global tolerance lets neighbours cross, which reads as slicing and flicker.
`order_flips_after_reduction` must be 0.

**Dense encoding only for evenly spaced samples.** Dense tracks drop their times and
resample on an even grid. A bake records whether its gaps were equal; a missing flag
means uneven, and a track with any off-grid key is sparse (`786ceb7`, `fe5634e`).
→ `test_encode_grid.py`

**Always sample the end frame** (`d7edaf0`).

---

## Visibility

**Cull whole elements for the whole clip.** Per-frame culling fades elements in across
whole gaps and pulls them out of ordering on some frames (`e5b0365`).
→ `test_occlude.py`

**Off-canvas is not the coverage cull's decision** — Cull Offscreen's margin exists so
geometry crossing the edge still draws.

**The frames either side of a visibility flip are drawn.** A hidden frame touching a
visible one keeps its own pose; the interior of a hidden run is pinned; a merged fill
with no pose of its own pops out 2% past its last real one instead of fading across an
invented pose (`3b03a77`, `fe5634e`). → `check_geometry.py`, `test_freeze_hidden.py`

**Boundary edges bypass `reachable`.** Dropping the exemption collapses thin open shells
such as the magnetometer boom to a hairline (`1ffb0ea`).

**The silhouette uses exact facing; hysteresis belongs to painting.** A held face
corrupts the silhouette it is built from (`b04f46b`).

---

## Welds

**A split part bakes identically to the whole part.** The criterion is identity, not
closeness: a cube cut in two is 0 differing pixels with chaining off. A welded edge is
judged by the dihedral across the weld, owned by one side, known to `reachable`, and
masked out of a merged outline's stroke (`bba5c1f`, `4ace104`, `2a4cf17`).

---

## Player

**The seam stroke is at least one device pixel**, measured from the context's own
transform (`lerpa@a252480`).

**Clear the canvas unconditionally**, then fill a background only if there is one.

**Frame-rate budget:** `stroke_width × scale × dpr ≤ 1.0` device pixel, or rasterisation
collapses ~5×.

**Hit tests reject by bounding box before tracing a path** (`lerpa@2279335`).
