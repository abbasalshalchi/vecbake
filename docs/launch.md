# Launch readiness — 16 October 2026

The question was what stands between VecBake/Lerpa and its first sale. This page
records what was found, from the repos, npm and the live site, and the order of work
proposed. Everything in §1 was checked on the day; §2 and §3 are judgement.

> **Correction, 17 October.** §2 and §3 answered the wrong question. The user's aim is
> the engine: good enough, as an engine, that people pay for what it makes. §1's facts
> stand — the stale npm player in particular is an engine-output fault a buyer sees —
> but the order in §3 was not adopted.

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

---

## 4. Listing on extensions.blender.org (31 October)

Checked against the [terms](https://extensions.blender.org/terms-of-service/) and the
[moderators' guidelines](https://developer.blender.org/docs/features/extensions/moderation/guidelines/).

**Run on Blender 5.2.3.** `blender --command extension validate` and `build` pass on
`addon/vecbake`, giving a 228,475 B zip with no `__pycache__`. Installed from that zip
into an isolated `BLENDER_USER_RESOURCES`, it enabled as
`bl_ext.user_default.vecbake`. Background bakes of the default cube (0.9 s) and the
cube plus a 2,562-vertex icosphere (153 s) wrote all five formats. The heavy bake ran
11 workers from the installed folder and wrote nothing into the add-on's directory.
The files were not opened and the panel was not used, so none of this covers the GUI.

**Changed for upload, later the same day**

| What | Why |
|---|---|
| Added `[permissions] files` and `website` to the manifest | The guidelines expect `files` for most I/O add-ons |
| `blender_version_min` 4.2.0 → 5.1.0 | Nothing from 4.2 to 4.5 has been baked on (§1). 5.1 was not rerun either: no 5.1 binary is installed here |
| Built `dist/vecbake-0.2.0.zip` | 228,554 B, 25 entries, no caches. SHA-256 `1C05347F0F7DC6671967D629E2E2F595E3EF84213379253357E63B88C3E30301` |

**That zip in a real window, on 5.2.3, from a clean profile**

- The VecBake tab opens and the whole panel draws, with no tracebacks.
- A simulated click on **Bake & Export** baked the default cube under a keyed camera and
  reported `12 elements (9 strokes, 3 fills), 12 drawn/frame, 289 keyframes` in the status
  bar. Simulated clicks are not dependable: most format toggles and one whole run's
  first click did not land, and only Preview Page ever toggled. Not chased, because the
  same runs show the button works.
- The operator the button calls, invoked in the window with every format on, wrote all
  seven files. From an unsaved file it refused and wrote nothing.
- The preview page, opened in headless Chrome from a panel bake, draws the cube.
- The Folder field shows red by default even though the bake succeeds and creates the
  folder. The add-on sets no alert there; why Blender paints it was not established.
- Not covered: macOS, Linux, a hand-driven mouse, and any bake beyond these scenes in the window.

**Could be raised in review**

- `preview/player.js` is 45 KB of MIT JavaScript in the zip, used only by the Preview
  format. Terms 3.6: "Source code of the extension must be fully written in Python."
  Terms 1.2 asks for CC0 on bundled assets. Neither names JavaScript, so ask the
  moderators before uploading.
- `_worker.py:60` writes to `sys.modules`, which the add-on guidelines forbid. It runs in
  a child Python, never inside Blender. Say so in the upload note rather than rewrite it.

**Clean in a search of the add-on's `.py` files:** `sys.path`, `threading`, `eval`, `exec`,
`bpy.data.objects`, `keyconfigs`, `__name__` as an ID, network calls and links in the UI.
ruff, which the guidelines ask for, is not installed here and was not run.

**Queue, as seen today.** No review time is published. The first entries awaiting review
were 25 minutes to 2 weeks old, plus one at 2 years.

**Listing images, in `listing/`.** The platform's model asks for a 256 x 256 PNG icon and a
featured image of at least 1920 x 1080 at 16:9 (`extensions/models.py` in its source);
both must exist before review. `icon.png` is the identity's box and shadow on its ground.
`featured.png` is `hero/voyager.svg`, baked 6 September, drawn by the real player;
`node scripts/film_listing_art.mjs <origin> listing` reshoots them. Voyager rather than
the hero because the repo records Voyager as a NASA model and records no source or licence
for Apollo–Soyuz, only that it came from Maya as a GLB. NASA's terms were not re-read and
the repo holds no download URL for the model.

**Moderators asked 31 October** about `player.js` in #extensions-moderators. Not answered yet.

**Agent support, built the same day.** `window_manager.vecbake` gives `describe`, `start`,
`status` and `cancel`; [agents.md](agents.md) is the guide. A request starts from the panel's
settings and never writes to them. No socket, no dependency, no download.

| Check | Result |
|---|---|
| `scripts/test_agent.py` | Refusals, measuring without writing, panel and agent bakes byte-identical in all six formats |
| `scripts/agent_connection_check.py`, the Lab server's own code in a second, isolated Blender | `start` answered in 48-80 ms; cancel, a second bake, a file load and a Selected source all behave |
| The same, through the real `blender-mcp.exe` over stdio | Tool listed, bake ran, files on disk, refusal arrived |
| Apollo-Soyuz (16,178 elements), copied, through `start` | Finished; the SVG hash matched before and after each slicing change |

The first Apollo run showed Blender unable to answer for 52 s in Chaining edges, and the next
19 s around Sizing. Both are now sliced (`chain.apply_iter`, `size_outlines`, `try_refine`,
`walk_shapes`), with the file unchanged at SHA-256 `E9AE897A...06AD`. What remains on Apollo:

| Stretch | Longest |
|---|---|
| Resolving occlusion, the frame the parent keeps for itself | 15.0 s |
| Writing files | 10.1 s |
| A Sampling boundary | 7.1 s |
| Reducing keyframes | 6.6 s |

All are far inside the bridge's 300 s (`blmcp/tools_helpers/connection.py`), and the window
is stuck for that long too, so they are worth slicing if someone complains. The Apollo bake
read 477 s before the slicing and 241-247 s after. Nothing in the change explains a halving,
and the runs were not alternated, so do not quote it as a speed-up.

Not tested through an agent: the user's own open Blender, which had unsaved work, so nothing
was installed in it. No model has driven this; every call was scripted.

**The zip to upload is `dist/vecbake-0.3.0.zip`**, which replaces 0.2.0: 234,719 B, 26 entries,
no caches, SHA-256 `99a67581bd1dc48244a0771083b851da0fb6f72a8f729c70cb920d7d928f11a0`. Installed
from that zip into an isolated profile, the full suite passes 30 of 30 on Blender 5.2.3. The
window checks above were made on 0.2.0 and not repeated, so the Cancel Bake button has not been
seen drawn. Blender 5.1, macOS, Linux and ruff remain unrun.

Found on the way and not fixed: the emitters report the size of their text, but on Windows
text mode turns each line ending into two bytes, so the panel's KB figure runs low and baked
files differ between operating systems. The agent reports sizes from the disk.

**For agent support, the rules.** `localhost` is allowed. A required external component is not, even
for an optional feature (terms 5.2), and neither is depending on another extension.
A socket in the add-on needs the `network` permission and a `bpy.app.online_access` check.
A long bake over MCP also blocks the GUI (`docs/gotchas.md`).
