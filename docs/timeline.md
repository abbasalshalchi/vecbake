# Timeline

The project from its first day, across `vecbake`, `lerpa` and `lerpa-website`.
Each phase names what was built, what went wrong, and where the detail lives.
Bugs are in [bugs.md](bugs.md); experiments in [experiments.md](experiments.md).

---

## 1–3 August — the baker, the player, the first dashboard

- **1 Aug.** The question: would a vector animation built from mathematical
  interpolation beat raster video? The add-on is written to bake a telecom tower
  scene for the Aegis Pulse dashboard. Stroke colour; Freestyle and crease overrides;
  chaining that dissolves collinear vertices, then chains straight **through
  junctions** (an X is two lines, not four). Keyframes turn out to be per element.
- **1 Aug.** Animation and resting states combined into one file (working name
  InterVG, `.ivg`), with a web tool as an npm package. First integration into
  Aegis Pulse: the trailing canvas (`transparent` never cleared) and a Nuxt config key
  that was silently ignored.
- **2 Aug.** Colours darker than their shaders — a linear-to-sRGB bug. The end frame
  showing behind the animation.
- **3 Aug.** Chained lines painted in front of dishes; then samples stepping over the
  dishes entirely; then the cycle breaker discarding 64% of the evidence. Lines thinned
  by their own faces → the **edge-on-face assertion**. Everything blanking on click.
  Relicensed the player to **AGPL with a commercial option**. The website gets a night
  sky theme. A free library of transitions is planned.

## 5–10 August — publishing, renaming, the butterfly

- **5–6 Aug.** Cloudflare deploy fails with nothing to deploy; the site becomes a real
  static site.
- **7–8 Aug.** **Curves**: lines to curves by screen angle or by Blender's shading. A
  cylinder rim chained one edge short; side fills spilling and short on opposite sides;
  a curve cache keyed by the wrong thing. Still-frame export. The player published to
  npm with type declarations and trusted publishing.
- **9 Aug.** Alpha from shader colours; see-through materials. Trademark and domain
  search; **InterVG becomes Lerpa**, the add-on stays VecBake, the package becomes
  `lerpa-player` after npm's typosquat filter refuses `lerpa`. Three repositories.
- **10 Aug.** Folders renamed to match. The **butterfly** is modelled and baked: wings
  missing under Backface Cull; **merged fills** so a silhouette is one element;
  loose-edge curves; thinning that reaches fills (`402b156`). The swarm demo is laggy —
  1,320 fill calls a frame. The terrarium demo is designed: two flight loops and a
  forward/reverse transition between top and side views.

## 11–17 August — the demo ships, the city, the preview

- **11 Aug.** Merged fills fall back per face when they cannot hold order or never ring
  (`c7c362e`, `8a78377`). Fill overlaps sampled by pixels (`069d762`). Butterfly demo
  fixes. Bake output untracked.
- **13–15 Aug.** The Lerpa brand: logo concepts, then the industrial direction, the
  **hi-vis vest identity**, the box-as-L lockup and the Lineal wordmark.
- **16 Aug.** A city of 6,144 boxes takes forever to bake → **Cull Hidden**. Flicker and
  translucency in an opaque scene, from per-frame culling and from dense encoding of
  unevenly spaced frames. The **preview page** an animator can double-click (`d9df75e`).
- **17 Aug.** The city is too heavy for a hero, and the wrong genre for it.

## 18–22 August — Voyager, the audit, parallel baking

- **18–19 Aug.** The hero becomes a spacecraft you can turn and take apart. The player
  gains group opacity, bounds and framing (`lerpa@56b4d61`). Lines leaping between parts
  with Curves on (`78a1b8e`). Culled frames mixing ranks and camera distances
  (`2687985`).
- **20 Aug.** The golden-record stray line — the cycle policy that fixed it and was
  reverted because edge widths flickered (`9fbb973`, `38fb353`). Edge-on shafts missed
  by sampling (`68585a4`). **The occlusion audit**, which finds what a person spotting
  frames finds (`f16afb5`).
- **21 Aug.** Seam hairlines too thin when zoomed out; closed chains missing their
  closing segment. The NASA Voyager model is loaded, simplified and split.
- **22 Aug.** **Ordering and culling in worker processes**: 66.9 → 8.9 min. On a
  seven-primitive lab scene, merged outlines are rebuilt around slots as positions
  (`e6be3af`, `a47c171`) and draw their own silhouettes (`0effe92`). Back on Voyager:
  45% of outlines self-intersect, the union-boundary approach, tangles, flicker.
  "Fix, don't patch."

## 23–26 August — the merge rewrite

- **23 Aug.** Welding endpoints, orienting walks, facing hysteresis, loop/hole swaps.
  Then **"this project has turned into a makeshift code full of bandaids and duct
  tape"**: the union approach is reverted (`2f3e20a`), and ordering moves to the
  **centre of the shared region** (`9b58e0e`). Merge only what never fails (`5fae344`).
- **24 Aug.** part_042's edges rotating between keyframes; a subframe probe against
  Blender's own silhouette (`e865e00`); anchored placement; the stick opening to four
  times its area; monotone matching; the **gap test** that refuses what faces would draw
  better (`fb3fce1`). "v_judge is the first acceptable merging." Merged to `main` with
  the earlier attempts kept as branches.
- **25 Aug.** Pinched and split silhouettes merge (`f7a03c9`); the gap test judges the
  real points (`ab6b5e9`). Your idea — place points by the area they get wrong — is
  built and measured.
- **26 Aug.** The area experiment arc: harness correction, substepping as a lottery,
  exact solving showing that per-frame optimality is the wrong target, a per-object gate
  (`8a6b337` … `2ddf47f`). Sizing point counts between keyframes (`9829204`). Tube bores
  bridged (`6e69a51`). Cubic time interpolation built and reverted.

## 28–31 August — occlusion, Blender 5.2, performance

- **28 Aug.** Steam updates Blender to 5.2.1 mid-session. Both ordering checks turn out
  unable to produce a number (`179c836`). Merged parts ordered by their real surface,
  and their edge assertions landing again: ray cast 4.52% → 2.24% (`6274add`).
- **29 Aug.** The 5.2 modifier-input change that silently rewrote a bake (`320f320`).
  Three unrunnable tests fixed (`3ef8aca`). Two ways not to reduce what cycle breaking
  discards (`3f9409d`).
- **30 Aug.** The deadlock measured: one SCC of 5,215 elements. Incremental SCC,
  two-phase ordering, stroke-vs-stroke and probe smoothing each measured and rejected
  (`c0c2e37` … `72b1c78`). A decimated Voyager changes no output number. Chaining's
  facing key kept (`ebe60f8`).
- **31 Aug.** Lerpa against three.js (5–9× slower); the frame budget; packed-key sort and
  OffscreenCanvas (`lerpa@29636db`). The **hairline cliff**. The website rebrand commit
  with its design work (`lerpa-website@ea94d5b`).

## 1–7 September — welds, the Voyager hero

- **1 Sep.** Stroke culling, off by default (`84d147b`). **Weld Seams**: split a part
  without drawing the cut (`bba5c1f`). Pricing links and a licence page on the site.
- **3 Sep.** Chevrons attributed to a fill, then corrected to a real crease (`87f01f5`).
  `reachable` taught about welds (`4ace104`). A cube cut in two must bake like the cube.
- **5 Sep.** The cube bakes like the cube: 1,710 → 4 pixels (`2a4cf17`, `b7def3d`). The
  preview's player synced and a merged-fill marker added. **Voyager in the hero**, turned
  by the pointer and taken apart by clicking (`lerpa-website@ca1794d`).
- **6 Sep.** Parts jumping while they turn; stray lines that turn out to be real specks,
  then strokes flying between frames — found on a controlled scene and fixed at the
  frames either side of a visibility flip (`3b03a77`, `fe5634e`). `check_geometry.py`
  grades position exhaustively (`889c752`).
- **7 Sep.** Following a zoomed part exactly (`@e8b3159`); holding its apparent size
  steady with an exact orbit-distance fit (`d4a029e`, `@b244094`).

## 10–13 September — Apollo–Soyuz

- **10 Sep.** Apollo–Soyuz replaces Voyager: a docking argues for animation. A ±20°
  wobble by cursor position, baked as a 60° arc (`3ebea49`, `@acd56a4`). Every animation
  is its own 1D timeline. The rig outgrows its panel.
- **11 Sep.** The wobble follows the pointer exactly (`@4d1cc6c`). Why `apollo_000` and
  the `apollo_018` thrusters will not merge: two rings, and four pieces.
- **12 Sep.** **Merged fills with several subpaths**, proved on a lab scene of five
  shapes first (`42fa586`), and handed to the ordering pass (`cec7935`). `apollo_003`
  paints over fittings bolted to it: agreement weighting built and reverted; graph
  diagnostics rule out the spending side; splitting `apollo_003` works; the solar panels
  are cleared as a non-bug. The cause is located as the nearest-vertex depth read, and a
  triangle read is built (uncommitted).
- **13 Sep.** Its audit: 446 → 289 wrong pixels, at an apparent 2.4× the bake time.
  [Progress report](progress-2026-09-13.md). The site re-themed to the vest identity.
  These documents. Later: the 2.4× turns out to be machine load (CPU profiling puts the
  triangle read at ~1 s a bake); `apollo_018` confirmed fixed per frame; the ordering
  pass made 3× faster with bit-identical ranks; committed and pushed (`20c2f71`,
  `8c53130`). A screening audit, now 15 minutes rather than 40, shows splitting
  `apollo_003` still pays after the fix (289 → 214) because one rank cannot describe a
  part that wraps a body — granularity, not measurement. Automatic splitting by evidence
  of interleaving is specified and handed off, not built. [Audits](audits.md).
  That evening it is built in `scripts/` by another agent: right on a controlled scene
  where the 50% rule fails twice, but on Apollo it splits 39 bodies and scores 342
  wrong pixels against 214 — one cycle break is 261 of them. Rejected as built, open.
- **15 September, evening.** The docking module's between-keyframe artefact gets a
  chosen limit, at the user's call: Merge False Outline, a bake setting (default 160 px
  of visible false outline over the clip, ray-cast). `dock_001` and three Voyager parts
  fall back to faces; geometry offenders stay 0 and segment pairs drop (28 → 26,
  198 → 173). The slot sliding underneath is not fixed. [Audits §14](audits.md#14-merge-false-outline-limit).
- **16 September.** The blinking thruster and the see-through parts turn out to be one
  bug: a cycle breaker that gave up whichever constraint had the smallest depth gap,
  with no idea what breaking it would paint. It now gives up the pair that covers the
  least screen — area for two fills, length × width for a line in a fill. Ray-cast wrong
  pixels: Apollo 1,623 → 1,012, Voyager 3,018 → 2,105, for about a third more CPU in the
  ordering pass. Two simpler rules measured worse and are written down with it.
  [Audits §15](audits.md#15-drawing-order-the-blinking-thruster-and-the-see-through-parts).
- **16 September, evening.** The dashed rings round the docking module were not faces
  ordered over a seam but a line measured by the wrong points: chains reached the ordering
  pass thinned, their depth sagged behind the ring, and every face meeting the ring at a
  vertex was painted over it by a hair. Ordered along their vertices, with touching faces
  kept under the line, the rings are solid; nicked line on Apollo falls to a sixth.
  [Audits §16](audits.md#16-dashed-rings-lines-measured-by-their-drawing-points).
- **17 September.** The ring 1 line reported as still broken was hidden behind the Apollo
  cone all along — and the previous day's fix had drawn it over the cone, by pricing a
  broken touch at a flat line-width square. Two quicker fixes each broke something the
  controlled scenes then pinned (a tie-break lost the nicks; a vertex-only check let a
  sliver thin a whole edge). A touch is now priced by the line it would really uncover,
  and the result is better than the pushed version on every measure.
  [Audits §17](audits.md#17-the-joint-line-a-touch-priced-by-what-it-would-really-show).
- **17 September, later.** The user applied a Boolean so the docking parts stop
  intersecting. Measured at the same commit: ray-cast errors 1,019 → 1,005, those
  involving the docking parts 80 → 54, the joint clean by the ray cast; +112 elements and
  11 new between-keyframe offenders, all on the reshaped `dock_002`. The big crossing faces
  elsewhere are untouched, and so are their errors. [Audits §18](audits.md#18-the-docking-joint-after-the-users-boolean).
- **10 October.** A month of finished work that existed only in working trees goes in:
  the player that draws a merged fill's rings as subpaths (`lerpa@f7da28a`, with the
  guard it never had), the site's vest re-theme and re-baked hero, and a
  `.assetsignore` so 8 MB of lab output stops being part of lerpafiles.com. Two
  measurement tools lost with a scratch directory are rebuilt into `scripts/`
  (`geometry_signature.py`, `measure_line_truth.py`); the first immediately shows the
  Apollo blend has been edited since `ba4b8dd`, so §18's before/after can no longer be
  re-run from disk. Then the interleaving selector is measured against the breaker
  that replaced the one it lost to, and the September verdict reverses: 196 → 87 wrong
  pixels, better than the hand split, for +59% elements. The user reports the engine
  bell as see-through; it turns out to be two of the four largest errors in the scene,
  both `measured wrong`, and splitting clears them.
  [Audits §19](audits.md#19-the-interleaving-selector-measured-again-against-the-new-breaker).
- **16–17 October.** Asked to make the project good enough to sell, the first answer
  was a storefront plan; the user corrected it — "good engine wise". Recorded in
  [launch.md](launch.md), with the correction.
- **25 October.** The cross-model scorecard: five models the engine was never tuned on
  and the two heroes, one harness (`scripts/scorecard.py`), same settings. Building it
  turned up five ways the tools read clean on a model they were not built on — the
  worst in the add-on itself: the audit cast its rays from the scene's render size,
  and fanned them under an orthographic camera, so a missed ray read as a pass. Fixed,
  with `test_audit_rays.py`. Apollo then reproduces §19 to the pixel (196). Across the
  rest: keyframe geometry within 1.11 px everywhere; ordering errors in the heroes'
  range; new classes — curves and text never baked, backface cull on open surfaces, and
  bake time that follows input faces whether in shot or not. The 180k-face doughnut
  had not finished ordering after an hour and a half.
  [Audits §20](audits.md#20-the-cross-model-scorecard).
- **25 October, later.** The first class off the scorecard: curves, text and surfaces
  bake, from the same evaluated mesh every other read already used. A controlled scene
  bakes identically as curves and as their own meshes, element for element; flashbang's
  see-through curves go, 369 → 134 wrong pixels; Apollo and the Voyager come back as the
  identical drawing. The doughnut finished: every pixel right, 160,287 elements, 124 MB —
  none of its nine bodies merged, the multi-ring carry problem on the most-made model in
  Blender. [Audits §21](audits.md#21-curves-text-and-surfaces-baked).
- **25–26 October.** Why a torus will not merge. A controlled torus refuses at every
  camera height, two different ways: seen clearly, the gap test was scoring a two-ring
  outline as one loop with its rings joined (13.2 px for an outline drawn within 0.3 px)
  — the joining §10 found on the thrusters in September and never removed; edge-on, the
  outline really fails on a folded silhouette. The joining is gone: multi-ring outlines
  are judged by Merge False Outline alone, as sizing already did. The torus merges; the
  last three `apollo_018` thrusters merge after a month on the open list; flashbang
  halves (2,597 → 1,345 elements); Apollo's wrong pixels fall 196 → 180; nothing is newly
  refused anywhere. The doughnut's own tori are refused rightly — through a 20 mm lens
  their silhouettes split and rejoin between keyframes — and that is the open decision.
  [Audits §22](audits.md#22-why-a-torus-will-not-merge).
- **26 October, later.** How a piece that appears could carry, measured before building.
  A tilted torus whose hole opens and closes during the clip turns up a sizing fault
  first — whether a part was sized as several subpaths depended on its last frame — and
  it is fixed (Voyager +9 merges, creature `Joint` refused, Apollo's audit 180 → 193).
  Then four ways to park the absent hole: drawing it only from the moment it exists cuts
  the torus's birth-and-death false line 94% with nothing at the keyframes, and keying the
  whole outline at each topology event cuts the doughnut torus's event gaps from 6,217 to
  13 px. Neither is built: the doughnut's bodies also drift 842–13,962 px in gaps with no
  event, so it would still not merge; and keying at events is two more keyframes each,
  which is the user's call. Single-ring and multi-ring outlines turn out to be held to
  different bars. [Audits §23](audits.md#23-a-piece-that-appears-how-it-could-carry-measured-before-building).
