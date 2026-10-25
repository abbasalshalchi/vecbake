# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Print the cross-model scorecard from the scorecard.json files scorecard.py wrote.

    python scripts/scorecard_table.py out/scorecard

Markdown, one column per model, in the order given (or alphabetical). Each run
also checks itself: re-grading the logged gaps at the run's own margin has to
reproduce the audit's wrong-pixel count exactly, or the gap log is not seeing
what the audit saw and the 0.05 column means nothing.
"""

import json
import sys
from pathlib import Path


def load(root, names=None):
    rows = []
    for path in sorted(Path(root).glob("*/scorecard.json")):
        r = json.loads(path.read_text(encoding="utf-8"))
        if names is None or r["name"] in names:
            rows.append(r)
    if names:
        rows.sort(key=lambda r: names.index(r["name"]))
    return rows


def regraded(r, margin):
    for g in r.get("regraded", []):
        if abs(g["margin"] - margin) < 1e-12:
            return g
    return None


def table(rows):
    def get(r, *keys, fmt="{}", missing="-"):
        v = r
        for k in keys:
            if not isinstance(v, dict) or k not in v:
                return missing
            v = v[k]
        return fmt.format(v)

    lines = []
    head = "| | " + " | ".join(r["name"] for r in rows) + " |"
    lines += [head, "|---" * (len(rows) + 1) + "|"]

    def row(label, fn):
        lines.append(f"| {label} | " + " | ".join(fn(r) for r in rows) + " |")

    row("Status", lambda r: r["status"])
    row("Camera", lambda r: r["camera"]["mode"])
    row("Frames", lambda r: str(r.get("frame_range", "-")))
    row("Audit margin", lambda r: f"{r['margin']:.4f}" if "margin" in r else "-")
    row("Elements", lambda r: get(r, "elements", fmt="{:,}"))
    row("Bake CPU, s", lambda r: get(r, "bake_cpu_seconds", fmt="{:,.0f}"))
    row("File, MB raw / gz", lambda r: (f"{r['file_bytes'] / 1e6:.2f} / {r['gzip_bytes'] / 1e6:.2f}"
                                        if "file_bytes" in r else "-"))
    row("Peak memory, MB", lambda r: get(r, "peak_memory", "peak_working_set_mb", fmt="{:,}"))
    row("Overlap px tested", lambda r: get(r, "audit", "overlap_pixels_tested", fmt="{:,}"))
    row("Wrong px (own margin)", lambda r: get(r, "audit", "wrong_pixels", fmt="{:,}"))
    row("Wrong rate", lambda r: get(r, "audit", "rate_pct", fmt="{:.3f}%"))

    def at_005(r):
        g = regraded(r, 0.05)
        return f"{g['wrong_pixels']:,}" if g else "-"
    row("Wrong px at 0.05", at_005)
    row("Distinct errors", lambda r: get(r, "audit_distinct"))
    for cause in ("measured wrong", "order violated", "no constraint",
                  "occluder not drawn there"):
        row(f"  {cause}", lambda r, c=cause: (
            f"{r['audit_causes'][c]['errors']} ({r['audit_causes'][c]['pixels']} px)"
            if c in r.get("audit_causes", {}) else ("0" if "audit_causes" in r else "-")))
    row("Geometry offenders", lambda r: get(r, "geometry", "offenders"))
    row("  over 10 px", lambda r: get(r, "geometry", "over_10px"))
    row("  worst, px", lambda r: get(r, "geometry", "worst_px", fmt="{:.2f}"))
    row("Segments @0.5 offending", lambda r: get(r, "segments", "offending_pairs"))
    row("  over 3 px", lambda r: get(r, "segments", "over_3px"))
    row("  over 10 px", lambda r: get(r, "segments", "over_10px"))
    row("  worst, px", lambda r: get(r, "segments", "worst_px", fmt="{:.1f}"))
    row("Merged fills", lambda r: get(r, "meta", "merged_fills"))
    row("  fell back", lambda r: get(r, "meta", "merged_fallback_count"))
    row("Cycle breaks", lambda r: get(r, "meta", "order_cycle_breaks", fmt="{:,}"))
    return "\n".join(lines)


def self_check(rows):
    bad = []
    for r in rows:
        if "audit" not in r:
            continue
        g = regraded(r, r["margin"])
        if g is None or g["wrong_pixels"] != r["audit"]["wrong_pixels"]:
            bad.append((r["name"], r["audit"]["wrong_pixels"], g and g["wrong_pixels"]))
    return bad


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "out/scorecard"
    names = sys.argv[2].split(",") if len(sys.argv) > 2 else None
    rows = load(root, names)
    print(table(rows))
    problems = self_check(rows)
    print()
    print("gap log self-check:", "ok" if not problems else f"MISMATCH {problems}")
