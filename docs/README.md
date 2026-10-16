# VecBake and Lerpa: project notes

The main [README](../README.md) explains how the add-on works and how to use it.
These pages record how it got that way — what broke, what was tried, what was
learned, and what is settled — so none of it has to be rediscovered.

| Page | Read it when |
|---|---|
| [timeline.md](timeline.md) | You want the story, dated, across all three repos |
| [bugs.md](bugs.md) | Something looks wrong — it may have been seen, explained and fixed before, or ruled out as the model |
| [experiments.md](experiments.md) | You have an idea — it may already have been built and measured, with numbers |
| [invariants.md](invariants.md) | You are about to change ordering, encoding, visibility, merging or welds |
| [measurement.md](measurement.md) | You are about to believe a number |
| [audits.md](audits.md) | You want the run behind a paint-order or bake-time number, with its settings |
| [gotchas.md](gotchas.md) | Blender, Windows, the browser or npm is behaving strangely |
| [decisions.md](decisions.md) | You want to know why the product, name, licence, look or hero is the way it is — and the working principles |
| [progress-2026-09-13.md](progress-2026-09-13.md) | You are picking up the unfinished paint-order work |
| [launch.md](launch.md) | You want to know what stands between the project and a sale, as of 16 October |

## Where else the history lives

- **Commit messages.** From 10 August on, commits carry their measurements, and
  commits marked "No code changes" or "Comment only" exist to record a result.
  `git log --format='%h %ad %s%n%b' --date=short` reads as a lab notebook.
- **Code comments beside the tempting change.** Failed attempts are written into the
  source where someone would try them again — `order._depths_at`,
  `order._overlap_centre`, the cycle breaker and gap weighting in `order.resolve`,
  `bake._deep_bodies`, `merge._coverage_side`, `outline.py`. Search the add-on for
  `measured`, `tried` and `reverted`.
- **Tests that pin a trade.** `test_order_cycle.py` pins a known stray line and says why
  it is the lesser error; `test_deep_body.py` pins the cases the depth rule must refuse.
- **The other repos.** `lerpa/docs/NOTES.md` for the player; `lerpa-website/docs/NOTES.md`
  and `lerpa-website/design/` for the site and identity.

## Keeping these up to date

- A bug gets an entry when it is understood, not when it is fixed: symptom as
  reported, cause as measured, fix with its commit, and the guard.
- An experiment gets a row whatever the verdict. A negative result with numbers is the
  most useful kind.
- When a claim here turns out to be wrong, correct it in place and say so — several
  entries already record a first diagnosis that was withdrawn.
