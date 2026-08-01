"""Format-neutral intermediate representation.

The whole point of this module: the baker produces a `VecScene`, and every
emitter (player JSON, SVG, Lottie) consumes one.  Nothing in here knows about
bpy, and nothing in here knows about any output format.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Element kinds
STROKE = "stroke"   # open polyline, `npts` points
FILL = "fill"       # closed polygon, `npts` points


@dataclass
class Track:
    """A sparse animation channel.

    `values[i]` is the value at `times[i]` (seconds).  A value is either a
    float (scalar track) or a flat list of floats (vector track, e.g. geometry
    stored as [x0, y0, x1, y1, ...]).  A track with a single sample is
    constant and emitters are expected to collapse it.
    """

    times: list[float]
    values: list

    @property
    def constant(self) -> bool:
        return len(self.times) <= 1

    def sample(self, t: float):
        """Evaluate with linear interpolation, clamped at both ends."""
        n = len(self.times)
        if n == 0:
            return None
        if n == 1 or t <= self.times[0]:
            return self.values[0]
        if t >= self.times[-1]:
            return self.values[-1]
        lo, hi = 0, n - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.times[mid] <= t:
                lo = mid
            else:
                hi = mid
        t0, t1 = self.times[lo], self.times[hi]
        u = 0.0 if t1 <= t0 else (t - t0) / (t1 - t0)
        a, b = self.values[lo], self.values[hi]
        if isinstance(a, (int, float)):
            return a + (b - a) * u
        return [av + (bv - av) * u for av, bv in zip(a, b)]


@dataclass
class Element:
    """One persistent vector primitive, alive for the whole animation.

    `key` is derived from mesh topology (object name + edge/face index), which
    is what gives us correspondence across keyframes for free.  Elements never
    appear or disappear from the list -- they fade via the `opacity` track.
    """

    key: str
    kind: str
    npts: int
    group: str
    color: tuple[float, float, float]
    width: float = 1.0
    geo: Track = None          # vector track, flat xy pairs, pixel space
    opacity: Track = None      # scalar 0..1
    depth: Track = None        # scalar, camera-space distance (bigger = further)
    shade: Track = None        # scalar 0..1 multiplied into `color` (fills)

    def tracks(self):
        for name in ("geo", "opacity", "depth", "shade"):
            tr = getattr(self, name)
            if tr is not None:
                yield name, tr


def reverse_track(track: Track, duration: float) -> Track:
    """Mirror a track in time: t -> duration - t."""
    if track is None:
        return None
    times = [duration - t for t in reversed(track.times)]
    values = list(reversed(track.values))
    times[0] = max(0.0, times[0])
    return Track(times, values)


@dataclass
class VecScene:
    """A baked animation, ready to serialise."""

    width: int
    height: int
    fps: float
    duration: float
    elements: list[Element] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    background: tuple | None = None
    meta: dict = field(default_factory=dict)

    def reversed_copy(self) -> "VecScene":
        """The same animation played backwards.

        A pure transform of the baked keyframes -- no second bake, no second
        pass over the 3D scene. Exactly the sort of thing the format-neutral IR
        exists to make free.
        """
        flipped = []
        for el in self.elements:
            flipped.append(
                Element(
                    key=el.key, kind=el.kind, npts=el.npts, group=el.group,
                    color=el.color, width=el.width,
                    geo=reverse_track(el.geo, self.duration),
                    opacity=reverse_track(el.opacity, self.duration),
                    depth=reverse_track(el.depth, self.duration),
                    shade=reverse_track(el.shade, self.duration),
                )
            )
        meta = dict(self.meta)
        meta["direction"] = "reverse"
        return VecScene(
            width=self.width, height=self.height, fps=self.fps,
            duration=self.duration, elements=flipped, groups=list(self.groups),
            background=self.background, meta=meta,
        )

    def drawn_per_frame(self, samples=None):
        """How many elements are actually visible at each instant.

        The number that matters. `elements` counts everything visible in *any*
        frame, so it barely moves when you tighten the edge filter -- an edge
        that is a silhouette for one frame of an orbit still has to ship. What
        governs runtime cost, and what to watch when aiming for a element
        budget, is the peak drawn in a single frame.
        """
        if not self.elements:
            return []
        if samples is None:
            samples = max(2, int(round(self.duration * self.fps)) + 1)
        counts = []
        for i in range(samples):
            t = self.duration * i / max(1, samples - 1)
            counts.append(
                sum(1 for el in self.elements
                    if (el.opacity.sample(t) if el.opacity else 1.0) > 0.004)
            )
        return counts

    def stats(self) -> dict:
        keys = 0
        pts = 0
        for el in self.elements:
            pts += el.npts
            for _name, tr in el.tracks():
                keys += len(tr.times)
        strokes = sum(1 for e in self.elements if e.kind == STROKE)
        drawn = self.drawn_per_frame()
        return {
            "elements": len(self.elements),
            "strokes": strokes,
            "fills": len(self.elements) - strokes,
            "points": pts,
            "keyframes": keys,
            "drawn_peak": max(drawn) if drawn else 0,
            "drawn_avg": round(sum(drawn) / len(drawn)) if drawn else 0,
        }
