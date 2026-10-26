# Decisions

What was decided about the product, its name, its licensing, its look and the way
work on it is done — with the reasoning, so a decision can be revisited on its
merits rather than rediscovered.

---

## Working principles

These came from correcting work that went wrong, and they apply to everything else in
this repository.

- **Fix, don't patch.** "If something needs components to change states mid animation
  and keep being redrawn in different ways, then there is probably a problem being
  patched instead of fixed." (2026-08-22)
- **No special-case branches and tuned epsilons.** Many decision points and hard-coded
  epsilons fitted to one model will fail on the next, and cannot be turned into settings
  anyone can reason about. This is why the union-boundary merge was reverted
  (2026-08-23) and why a containment special case for `apollo_003` was rejected
  (2026-09-12).
- **Test on a scene you control first.** A new behaviour is proved on simple primitives
  whose right answer can be stated in advance, then checked on the real model.
  (2026-09-06, 2026-09-12)
- **The output has to hold up played slowly.** Artefacts between keyframes count as much
  as artefacts at them. More keyframes to hide an artefact is patching. (2026-08-24)
- **Merge only what never fails.** Chase the failures to reduce them; never merge
  something that fails in some frames. (2026-08-23)
- **Splitting geometry is the artist's job.** No automatic face or object splitting;
  the audit names crossing pairs and leaves the split to the model. (2026-08-20, 2026-08-23)
- **Measure against ground truth before claiming a fix**, and say plainly when something
  was reasoned rather than measured. See [measurement.md](measurement.md).
- **Keep failed attempts in the history.** Merges to `main` must not delete the branches
  that record previous attempts. (2026-08-24)

---

## Product

- **What it is:** a Blender add-on that bakes a 3D camera move into sparse 2D keyframes,
  and a web runtime that interpolates them. All 3D happens once, offline. The first use
  was replacing raster video transitions on a NOC-style dashboard (2026-08-01); it became
  a general-purpose web transition tool, not a telecom product (2026-08-03).
- **The file is a real SVG.** Animation data travels inside an `.svg` that still renders
  as a still image with no player — one file, no new extension, no MIME setup
  (2026-08-01, 2026-08-07).
- **No cloud baking.** Considered as copy protection and dropped (2026-08-03).
- **Other platforms get Lottie.** There is one runtime and it is the web one; iOS,
  Android, React Native and Flutter use Lottie exported from the add-on (2026-08-09).
  Lottie's layer order is static, so occlusion cannot flip mid-animation there.
- **Canvas 2D, no WebGL required.** A WebGL renderer was analysed and not built: its
  ceiling is ~2.5–3× unless interpolation moves to the GPU, and "no WebGL required" is
  something three.js cannot say (2026-08-31).
- **Temporal interpolation stays linear.** Cubic was built and reverted on bake time;
  storing every frame was measured and rejected (see [experiments.md](experiments.md#5-time-keyframes-and-file-size)).
- **A free library of ready-made transitions**, CC0, playable with the free runtime
  before anyone buys the add-on (2026-08-03). The butterfly is the first entry.

---

## Names

| Date | Decision |
|---|---|
| 2026-08-01 | Working name **InterVG**, `.ivg` — later dropped as a name coined by the assistant |
| 2026-08-09 | Add-on stays **VecBake**; format, player and brand become **Lerpa** — the bodymovin / Lottie split |
| 2026-08-09 | Site at **lerpafiles.com**, the LottieFiles pattern |
| 2026-08-09 | npm package **`lerpa-player`**: `lerpa` was rejected by npm's typosquat filter (too close to `lerna`), and `@lerpa/*` belongs to an unrelated project |
| 2026-08-10 | Folders renamed to match repos: `vecbake/`, `lerpa/`, `lerpa-website/`, `demo/` |

On-disk tokens are `data-lerpa="1"`, `<metadata id="lerpa-data">`,
`data-format="lerpa"`. Files baked before 9 August cannot be read.

The name Lerpa comes from *lerp*, although the player uses cubic spatial interpolation —
"photoshop isn't a shop either".

---

## Licensing and pricing

**26 October 2026: free and open source, both halves.** The add-on is free under
GPL-3.0-or-later and its repository goes public; the player is relicensed **MIT** from
0.5.0. The reasons, as the user set them out: real downloads were zero (all 54 a month were
the author's own), there is no company to sell a licence through and no lawyer to write
one, registering a company in Iraq costs millions of IQD, and the horizon is months, not a
year, because language models are starting to make such animations directly. The project's
value now is as a public credential and as the grader behind an RL environment;
AGPL and a paid tier only kept users away. Commissions
remain possible. What follows is the record of the earlier model, superseded.

- **Player: AGPL-3.0-or-later with a commercial licence** (2026-08-03). MIT let a
  closed-source product ship the runtime free, leaving nothing to sell; AGPL is the
  condition such a product cannot meet. Only possible while one author holds the whole
  copyright.
- **Add-on: GPL**, permanently, because it links against Blender's Python API. A copy
  someone buys is theirs to redistribute; it is sold, not given away (2026-08-03).
- **Baked files carry no obligation.** Only the runtime does.
- **Tiers:** Player free · Studio $59 once · Commercial $399/year · Delivered from
  $2,500. Buy buttons are mailto links until a hosted checkout exists.
- **Publishing** runs from GitHub Actions with trusted publishing and provenance; see
  [gotchas.md](gotchas.md#npm-and-publishing).

---

## Visual identity

| Date | Decision |
|---|---|
| 2026-08-03 | Website in a "purplish wizardy starry spacey" theme: night sky in dark, aged spellbook in light |
| 2026-08-13 | Logo exploration: node path with keyframe diamonds, a press "pressing 3D into 2D", a lettermark, an elongated box casting a shadow that reads as an L |
| 2026-08-13 | Direction shifts to **industrial**, leaning into engineering and medical animation |
| 2026-08-14 | Petroleum blue `#162033` tried and rejected; **"the visibility vest theme"** chosen |
| 2026-08-14 | Wordmark: the box **replaces the L**; lockup variant 01, baseline on the shadow's top edge |
| 2026-08-15 | Wordmark typeface: **Lineal** (Velvetyne, SIL OFL), outlined in Inkscape. Bahnschrift rejected for licensing (Windows font, no webfont or clear logo use); Diatype and Ginto are paid; Britannic Bold rejected |
| 2026-08-31 | Rebrand commit describes the industrial identity — but the committed stylesheet stayed astral |
| 2026-09-13 | Site re-themed to the vest identity |

### The vest identity
Defined in `lerpa-website/design/identity-hivis.html`.

- **Two materials.** Hi-vis fabric shouts; retroreflective banding reads. The mark is
  the same structure: a flat hi-vis shadow cast by a machined steel box.
- **Palette:** ground `#0D0D10`, panel `#141419`, rule `#292930`, ink `#F1F0EC`, hi-vis
  `#D2EF2B`, steel `#B7BCC3` / `#6A707A` / `#33373F`.
- **Hi-vis is progress and action. Red, amber and green are reserved for data**, which is
  why yellow-green won over hi-vis orange: orange sits too close to warning amber.
- **Hi-vis fails on white** (about 1.3:1). The site is dark-only, and on light grounds
  the mark sits in its own dark tile, with `#9DB800` as the fallback where a tile is
  impossible.
- **The banding device** — alternating hi-vis and silver — is the ownable element that
  is not the logo: page top, card heads, dividers.
- **Artwork:** `vecbake/out/logo design final.svg` (lockup, layer 3) and
  `out/logo design icon big transparent.svg` (icon, layer 4). The site's copy is
  `lerpa-website/public/brand/lerpa-lockup.svg`. `out/` is not in git.
- **Copy** moved from spell vocabulary to press vocabulary with the theme: "Press a 3D
  move into a single SVG".

---

## Website hero

| Date | Decision |
|---|---|
| 2026-08-13 | Butterfly asset on the site, tilted to fly upward, with its own page and a zip of three clips |
| 2026-08-17 | City flyover rejected for the hero: ~2,000 fills a frame, and it reads as architecture, not engineering |
| 2026-08-18 | A spacecraft you can turn and take apart, with click-to-zoom on parts and nested assemblies; a NASA Voyager model |
| 2026-08-31 | Free rotation is impossible from a Lerpa file; rotation on one axis is a scrubbed orbit |
| 2026-09-05 | Voyager in the hero: turned by the pointer, tilted 45° in CSS, zoom tree of segments and parts, occlusion cull off so hidden parts can reappear, fills in the background colour and strokes in the highlight |
| 2026-09-10 | **Apollo–Soyuz** instead: a docking is a better argument for animation than a turntable. Full turn replaced by a **±20° wobble tied to cursor position**, baked as a 60° arc |
| 2026-09-10 | **Every animation is its own 1D timeline.** A docking does not share a timeline with the wobble: when it starts, the cursor position says which wobble state to start from, and the wobble stops |
| 2026-09-10 | The rig may overflow its panel and pass behind the text; the pointer is read across the whole hero |
| 2026-09-11 | The wobble follows the pointer exactly; easing only on the return to centre |

---

## Baking choices for the hero models

- **Edge mode SHARP at 30°** for Apollo–Soyuz: the model came from Maya as a GLB with no
  Smooth by Angle, so SHADING drew only silhouettes.
- **The model was split into 91 loose parts**, because Lerpa's identity is per object and
  a single mesh gives one group — nothing to click.
- **Assemblies are read from a colour-banded render**, not from gaps between parts.
