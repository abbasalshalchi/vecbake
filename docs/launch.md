# Launch readiness — 16 October 2026

The question was what stands between VecBake/Lerpa and its first sale. This page
records what was found, from the repos, npm and the live site, and the order of work
proposed. Everything in §1 was checked on the day; §2 and §3 are judgement.

---

## 1. Where things stand

| Piece | State | Checked by |
|---|---|---|
| Add-on release | `dist/vecbake-0.1.0.zip`, built 9 August. Every ordering, merging and weld fix since is unreleased | `ls dist`, manifest `version = "0.1.0"` |
| Player on npm | `lerpa-player@0.4.0`, published 28 August. Four player commits since are unpublished, including `f7da28a` (merged fill rings as subpaths) and `dc05cca` (merged outline skips its own cut) | registry, `git log v0.4.0..HEAD` |
| Add-on vs npm player | The add-on writes `sp` (rings of a merged fill) since `42fa586`; 0.4.0 does not read it, so it traces every ring as one path — the docking-module wedge. **A buyer who bakes today and installs from npm gets that artefact** | reasoned from the `f7da28a` message; not re-run against 0.4.0 |
| npm downloads | 54 in the month to 12 October | npm downloads API |
| Buying | The site's Buy buttons are `mailto:` links. No hosted checkout | `lerpa-website/public/index.html` |
| Library | 1 of 7 entries real (butterfly). Orbit, Flip, Burst, Stack, Lathe, Globe have read "Baking · Soon" since August | same |
| Source | `lerpa` public; `vecbake` private | GitHub |
| Blender range | Manifest says `blender_version_min = "4.2.0"`. Installed here: 3.6, 4.1, 5.1, 5.2. Nothing in the 4.2–4.5 range has been installed or baked on | `%APPDATA%\Blender Foundation\Blender` |
| User-facing docs | The README is 1,105 lines and mostly how the engine works. There is no quick start a stranger can follow in five minutes | `README.md` headings |

Since 1 August there are 142 commits in `vecbake`. From September on, the work has
been paint order and merged outlines on the hero models — ray-cast wrong pixels on
Apollo 1,623 → 1,012, Voyager 3,018 → 2,105. Nobody outside the project has baked a
file.

---

## 2. Reading of it

The engine is not what stands between this and a sale. Apollo–Soyuz is 91
interpenetrating parts from a Maya GLB; a first buyer's scene is a product, a logo or
a UI card under a camera move. What has never been tested is the path a stranger takes:
install, bake their own model on default settings, play it with the npm player, pay.
Three links of that path are known broken or missing (the stale player, the stale
zip, the mailto checkout) and the other two are untested.

So "good enough" is defined here as: a stranger completes that path without
help. Engine work is in scope when it fails that test, and otherwise waits for a
user's report.

---

## 3. Proposed order

1. **Stranger test, which is also the library.** Build the add-on from HEAD, install
   it fresh into 4.2 LTS and 5.2, and bake the six placeholder library entries from
   scratch on default settings. Play each through the published player path. Every
   failure is a ship blocker; fix those and only those. Done when the six files play
   and go up as CC0 — the library goes from 1 to 7 real entries.
2. **Release.** `lerpa-player` 0.5.0 with the four unpublished fixes; `vecbake` 0.2.0
   zip. Both need the user's OK to publish.
3. **Checkout.** Replace the mailto links with a hosted checkout that delivers the
   zip and a licence key. Which processor depends on where payouts can be received —
   the user's decision.
4. **Quick start.** One short page: install, bake the default cube under a camera
   move, drop the file into a page. The engineering README stays as reference.
5. **Sell the Delivered tier by hand.** One commission at $2,500 is worth about 42
   Studio copies, and it shows what buyers actually ask for.
6. **Launch.** The README's "three things that were hard" is the Show HN post as it
   stands; a Superhive listing; r/blender and Blender Artists with a short capture of
   the hero.
