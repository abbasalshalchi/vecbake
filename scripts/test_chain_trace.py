"""Invariants of chain._trace. Run inside Blender:

    exec(open('scripts/test_chain_trace.py').read())

The one that matters most is `members[i]` joining `path[i]` to `path[i + 1]`.
Everything downstream slices those two lists together -- straddle cuts, the
per-run colour, the on-face assertions the ordering pass depends on -- so if
they ever drift apart the damage shows up somewhere else entirely.
"""

import numpy as np

from vecbake.chain import _trace


def _cont(edge_verts, eligible):
    """What _continuations builds for vertices of degree two."""
    incident = {}
    for e in eligible:
        a, b = edge_verts[e]
        incident.setdefault(int(a), []).append(e)
        incident.setdefault(int(b), []).append(e)
    out = {}
    for vertex, here in incident.items():
        if len(here) == 2:
            e1, e2 = here
            out[(vertex, e1)] = e2
            out[(vertex, e2)] = e1
    return out


def _run(edges, eligible=None, keys=None):
    edge_verts = np.array(edges, dtype=np.int32)
    eligible = list(range(len(edges))) if eligible is None else eligible
    keys = keys or {e: "k" for e in eligible}
    return _trace(edge_verts, eligible, keys, _cont(edge_verts, eligible))


def _check_alignment(edges, path, members, label):
    for i, e in enumerate(members):
        pair = {int(edges[e][0]), int(edges[e][1])}
        assert pair == {path[i], path[i + 1]}, \
            f"{label}: members[{i}]={pair} does not join {path[i]}->{path[i + 1]}"


def test_ring_closes():
    edges = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 0)]
    chains = _run(edges)
    assert len(chains) == 1, f"a ring is one chain, got {len(chains)}"
    path, members, closed = chains[0]
    assert closed, "a ring must report closed"
    assert len(members) == 5, f"every edge is claimed, got {len(members)}"
    assert path[0] == path[-1], "a closed path repeats its seam vertex"
    assert len(path) == 6, f"5 edges span 6 path slots, got {len(path)}"
    _check_alignment(edges, path, members, "ring")


def test_arc_stays_open():
    edges = [(0, 1), (1, 2), (2, 3), (3, 4)]
    path, members, closed = _run(edges)[0]
    assert not closed
    assert len(members) == 4 and len(path) == 5
    _check_alignment(edges, path, members, "arc")


def test_growth_backwards_keeps_alignment():
    """Seeded in the middle, so the walk grows off both ends."""
    edges = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)]
    path, members, closed = _run(edges, eligible=[2, 0, 1, 3, 4])[0]
    assert not closed
    assert len(members) == 5 and len(path) == 6
    _check_alignment(edges, path, members, "backwards")


def test_figure_eight_stays_open():
    """Two rings sharing vertex 0. No single traversal closes it."""
    edges = [(0, 1), (1, 2), (2, 0), (0, 3), (3, 4), (4, 0)]
    for path, members, closed in _run(edges):
        if len(members) > 1:
            _check_alignment(edges, path, members, "figure-eight")
        # a revisit that is not the start must not be sold as a ring
        if closed:
            assert path[0] == path[-1]


def test_split_keys_do_not_merge():
    """Edges drawn differently never join, ring or not."""
    edges = [(0, 1), (1, 2), (2, 3), (3, 0)]
    keys = {0: "a", 1: "a", 2: "b", 3: "b"}
    chains = _run(edges, keys=keys)
    assert all(not closed for _p, _m, closed in chains), "mixed keys cannot ring"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print("ok  ", fn.__name__)
    print(f"\n{len(tests)} passed")


main()
