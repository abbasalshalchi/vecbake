# Environment and workflow gotchas

Things about the tools around VecBake — Blender, Windows, the browser, npm — that
each cost real time and fail in ways that do not point at their cause.

---

## Blender

### The installed add-on is not the repo
The add-on runs from
`%APPDATA%\Blender Foundation\Blender\<version>\extensions\user_default\vecbake`,
a copy. Editing `addon/vecbake/` changes nothing in Blender until it is copied
there. Headless bakes (`blender -b file.blend --python x.py`) load the installed
copy too, which makes it easy to measure old code while believing it is new. After
installing, delete that folder's `__pycache__`, and compare file contents rather
than timestamps.

Copy the whole folder, not just the `.py` files. On 13–14 September every reinstall
copied Python only, so the installed `preview/player.js` stayed a version without
multi-ring fills. Every preview drew a merged fill's rings as one joined path, and a
wedge across the Apollo docking module was chased as a merge bug for hours. Check the
whole tree:
`for f in $(cd addon/vecbake && find . -type f -not -path '*/__pycache__/*'); do cmp -s addon/vecbake/$f "$INSTALLED/$f" || echo $f; done`.

### A running Blender keeps its modules
Copying files does not reload already-imported modules. `bpy.ops.script.reload()`
does not touch `bl_ext.*`, and a module object keeps its old `__doc__` even when its
functions are current — compare bytecode, not docstrings. The only reliable reload is
`addon_disable`, purge `sys.modules`, `addon_enable`.

### Reloading resets the scene's settings
Unregistering the PropertyGroup brings every `scene.vecbake` value back at its
default. On 3 September that wiped `out_dir`, `weld_seams` and `merge_fills` on a live
session. Dump every writable property to JSON first, restore after, and check a known
default (`out_dir` reverting to `//vecbake/`) to confirm.

### Steam updates Blender under you
Steam moved 5.1.2 → 5.2.1 LTS mid-session on 28 August. Extensions are per version,
so the add-on under `5.1` stopped loading in 5.2, and the bundled Python moved with
it: tests now run with `Blender/5.2/python/bin/python.exe`. A portable 5.1.2 kept
beside it is what made the 5.2 regression findable at all — the `.zip` builds are at
`https://download.blender.org/release/`.

### Blender 5.2 moved geometry-node inputs
In 5.1 a modifier input was `mod["Input_1"]`. In 5.2 it is
`mod.properties.inputs.Input_1.value` — and the old subscript **does not raise**, it
reports the socket unset. A reader with a default substitutes it and reports success.
That replaced the Voyager's 73.59° Smooth by Angle with a 30° fallback on all 310
objects: +1,095 strokes, 15.7 → 17.5 MB, bake 12.5 → 68 min. There was no performance
regression to find. Read both locations, new first; `shading_angle_guessed` in the
bake report says when neither worked. `320f320`.

### Other bpy breakages from 4.x to 5.x
- Slotted actions: `action.fcurves` is gone — walk
  `layers[].strips[].channelbag(slot).fcurves`.
- The compositor is `scene.compositing_node_group`; a `NodeGroupInput` is not wired to
  the render and silently outputs white.
- `file_format = 'FFMPEG'` raises until `media_type = 'VIDEO'`.
- Menu sockets take display strings (`'Fog Glow'`), not identifiers.
- Only EEVEE is registered unless Cycles is enabled.
- `-noaudio` has one dash; `--noaudio` is parsed as a filename.
- `ANGLE` properties are stored in radians and capped at π.
- `mat.use_nodes = False` does not stick (see [bugs.md](bugs.md#every-material-flattened-to-the-same-grey)).
- `bpy.path.abspath("//out/")` returns a *relative* `out/` for an unsaved file, which
  then writes into Blender's install folder. Test `os.path.isabs()`.

### A bake through the MCP blocks the GUI
Running a long bake over the Blender MCP connection freezes the user's session and
times the connection out. Run bakes headless in a separate Blender process instead.

### Kill Blender by PID, never by name
`taskkill /IM blender.exe` kills every Blender, including the user's own session.
Find the headless process by its command line and kill that PID.

### Blender MCP is two pieces
The Blender Lab add-on listens on `localhost:9876` and speaks null-delimited JSON (not
MCP); the Claude Desktop connector binary is the MCP bridge. A connector enabled in the
Desktop UI does nothing for Claude Code, whose servers live in `.claude.json`. If tools
are missing: Blender running → add-on server started → port 9876 listening →
`claude mcp list`.

### The `imp` traceback is noise
Every headless run prints `ModuleNotFoundError: No module named 'imp'` at startup.
It is harmless — and it matches a naive `grep Error`.

---

## Files and folders

### Renaming fails with "Access denied"
Renaming a folder under `Program Files` fails if any shell's working directory is
inside it. That is the whole error — not a permissions problem.

### Nested repos
`vecbake/` gitignores `lerpa/`, `lerpa-website/` and `demo/`, which are separate
repositories, plus `out/` and `snapshots/`, which are bake output. **Nothing in `out/`
is backed up** — the logo exports from Inkscape live there
(`out/logo design final.svg`, `out/logo design icon big transparent.svg`).

### Three copies of the player
`lerpa/src/player.js` is the source. `addon/vecbake/preview/player.js` is inlined into
every `.preview.html`; `lerpa-website/public/lerpa/` is served by the site. Neither
copy tracks the source. After a player change: copy it to both, run
`scripts/sync_player.py`, and remember an existing preview only picks it up by
re-baking. That makes four: the preview inlines the *installed* add-on's copy, not the
repo's.

### PowerShell 5.1 has no `&&`
`cd x && git commit` is a parser error in Windows PowerShell 5.1. Use `;` and
`if ($?) { ... }`, or run it from Git Bash.

---

## The browser

### A hidden tab does not paint
`requestAnimationFrame` is suspended and screenshots time out when a tab is not
visible — in Chrome when the user is on another tab, and in the in-app browser pane,
which can report `document.hidden` even when fronted. Measure frame rates in a visible
browser.

### Module scripts do not load over `file://`
Which is why the preview page inlines the player: an animator should be able to
double-click a file.

### The dev server's root is the repo
`scripts/devserver.py` serves `vecbake/` on port 8765 and falls back to
`lerpa-website/public` only when the repo has no such path. `/` lists the repo; the
site is `/index.html`.

---

## npm and publishing

- **`npm login` and `npm publish` are the user's to run.** They need the user's
  credentials and 2FA.
- **Tokens never pass through anyone else.** Publishing runs in GitHub Actions through
  trusted publishing with short-lived OIDC; do not create a token with "Bypass
  two-factor authentication".
- The first version of a package must be published by hand — npm cannot configure a
  trusted publisher for a package that does not exist yet.
- A doubled `npm version` leaves a stray tag that would publish backwards if released.
  `lerpa/RELEASING.md` has the steps.
- A 404 from `registry.npmjs.org/<name>` does not mean a name will publish: the
  typosquat filter rejected `lerpa` as too close to `lerna` at `PUT` time, and
  `@lerpa/*` was taken by an unrelated project. Probe every name one edit away.
- Pushing to any remote is outward-facing and needs an explicit go-ahead each time.

---

## Cloudflare

- The build runs `npx wrangler deploy`, which needs `wrangler.jsonc` to know what to
  upload; loose placeholder files do not help.
- A page served at `/` must be `index.html`, and a complete document — an artifact
  fragment renders in quirks mode.
