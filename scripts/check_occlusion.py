# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Thin wrapper around the add-on's own audit, for scripting it.

The audit itself lives in `vecbake/audit.py` and is reachable from the panel --
**Audit > Check Occlusion** -- which is where it belongs for anyone baking
rather than working on the baker. Nothing here is needed to use it.

This exists for the cases the panel does not cover: a headless run, a
regression check over a specific frame list, or reading the returned dict
rather than the summary.

    exec(open(r'.../scripts/check_occlusion.py').read())
    report = run(frames=[0, 12, 24])
"""

from __future__ import annotations

try:                                   # installed as an extension
    from bl_ext.user_default.vecbake import audit
except ImportError:                    # loaded straight from the add-on folder
    from vecbake import audit

run = audit.run


if __name__ == "__main__":
    run()
