// SPDX-License-Identifier: AGPL-3.0-or-later
// Lerpa -- Copyright (C) 2026 Abbas Alshalchi
// Also available under a commercial licence; see COMMERCIAL.md
/**
 * Lerpa player -- Canvas 2D renderer for .lerpa vector animations.
 *
 * Per frame: interpolate sparse 2D keyframes, painter-sort by a baked order
 * value, draw. No projection, no camera, no z-buffer -- all of that happened
 * once at bake time.
 *
 * @module
 */

const STROKE = 0;

const HEX_RE = /^#([0-9a-f]{3,8})$/i;
const FUNC_RE = /^(rgba?|hsla?)\(([^)]*)\)$/i;

const clamp = (v, hi) => (v < 0 ? 0 : v > hi ? hi : v);

/**
 * Split the inside of a colour function.
 *
 * `rgb(1, 2, 3)`, `rgb(1 2 3)` and `rgb(1 2 3 / 40%)` all have to work: the
 * legacy comma form and the modern space form are both valid CSS. Everything
 * after `/` is alpha, which we drop -- see parseColor.
 */
const splitArgs = (body) => body.split('/')[0].trim().split(/[\s,]+/).filter(Boolean);

/** `50%` -> half of `full`; a bare number passes through. */
function channel(token, full) {
  const v = parseFloat(token);
  if (!isFinite(v)) return 0;
  return token.endsWith('%') ? (v / 100) * full : v;
}

/**
 * Saturation / lightness, always as 0..1.
 *
 * CSS Color 4 lets these be written bare, where `70` means the same as `70%`,
 * so both divide by 100.
 */
const slPart = (token) => clamp((parseFloat(token) || 0) / 100, 1);

/** Hue in any CSS angle unit, folded into 0..360. */
function hueDegrees(token) {
  const v = parseFloat(token) || 0;
  if (/turn$/i.test(token)) return (((v * 360) % 360) + 360) % 360;
  if (/rad$/i.test(token)) return ((((v * 180) / Math.PI) % 360) + 360) % 360;
  if (/grad$/i.test(token)) return (((v * 0.9) % 360) + 360) % 360;
  return ((v % 360) + 360) % 360;
}

function hslToRgb(h, s, l) {
  const k = (n) => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = (n) => l - a * Math.max(-1, Math.min(k(n) - 3, 9 - k(n), 1));
  return [Math.round(255 * f(0)), Math.round(255 * f(8)), Math.round(255 * f(4))];
}

let probeCtx;   // lazily made once, module-level, only for the fallback below

/**
 * Last resort: let the browser parse it.
 *
 * Covers named colours ('tomato'), and anything newer than this file knows
 * about -- lab(), oklch(), color-mix(). Returns null when there is no DOM, so
 * server rendering still works.
 */
function parseViaCanvas(css) {
  if (typeof document === 'undefined') return null;
  if (probeCtx === undefined) {
    try {
      const c = document.createElement('canvas');
      c.width = c.height = 1;                    // one pixel is all we read
      probeCtx = c.getContext('2d', { willReadFrequently: true });
    } catch { probeCtx = null; }
  }
  if (!probeCtx) return null;
  // Assigning an invalid colour to fillStyle is a no-op -- it silently keeps
  // whatever was there. So assign over two different knowns: if the browser
  // understood the value both land on it, and if it did not they differ.
  probeCtx.fillStyle = '#000000';
  probeCtx.fillStyle = css;
  const first = probeCtx.fillStyle;
  probeCtx.fillStyle = '#ffffff';
  probeCtx.fillStyle = css;
  if (first !== probeCtx.fillStyle) return null;

  // Paint one pixel and read it back, rather than re-parsing the serialised
  // fillStyle. Wide-gamut values do not serialise to hex or rgba() -- an
  // oklch() stays an oklch() -- so string round-tripping drops exactly the
  // colours this fallback exists to catch. A pixel is always sRGB bytes.
  // 'copy' rather than the default blend, so a translucent colour replaces the
  // pixel instead of compositing with whatever was under it.
  const prev = probeCtx.globalCompositeOperation;
  probeCtx.globalCompositeOperation = 'copy';
  probeCtx.fillRect(0, 0, 1, 1);
  probeCtx.globalCompositeOperation = prev;
  const d = probeCtx.getImageData(0, 0, 1, 1).data;
  return [d[0], d[1], d[2]];
}

/**
 * CSS colour -> [r, g, b], each 0..255.
 *
 * Handles `#hex` in 3, 4, 6 and 8 digits, plus `rgb()`/`rgba()` and
 * `hsl()`/`hsla()` in both the comma and space syntaxes; anything else goes to
 * the browser. Alpha is parsed off and discarded, because element opacity is
 * its own animated track and a colour carrying alpha too would multiply twice.
 *
 * An unrecognised value comes back black rather than throwing: one bad colour
 * should not take down the whole animation.
 *
 * This used to be "find the first three numbers in the string", which quietly
 * read `hsl(210 70% 60%)` as `rgb(210, 70, 60)` -- a hue becoming a red channel,
 * wrong but plausible enough to look like a design choice rather than a bug.
 */
function parseColor(css) {
  if (typeof css !== 'string') return [0, 0, 0];
  const s = css.trim();

  const hex = HEX_RE.exec(s);
  if (hex) {
    const h = hex[1];
    // #rgb and #rgba: every digit doubles. The 4th is alpha -- it must be
    // recognised so it is not mistaken for part of the colour.
    if (h.length === 3 || h.length === 4) {
      return [parseInt(h[0] + h[0], 16), parseInt(h[1] + h[1], 16), parseInt(h[2] + h[2], 16)];
    }
    // #rrggbb and #rrggbbaa. Slicing rather than shifting one big integer,
    // because 8 digits overflows the sign bit and `>>` would return nonsense.
    if (h.length === 6 || h.length === 8) {
      return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
    }
    return [0, 0, 0];                       // 5 or 7 digits is not a colour
  }

  const fn = FUNC_RE.exec(s);
  if (fn) {
    const args = splitArgs(fn[2]);
    if (args.length < 3) return [0, 0, 0];
    if (fn[1].toLowerCase().startsWith('rgb')) {
      return [
        Math.round(clamp(channel(args[0], 255), 255)),
        Math.round(clamp(channel(args[1], 255), 255)),
        Math.round(clamp(channel(args[2], 255), 255)),
      ];
    }
    return hslToRgb(hueDegrees(args[0]), slPart(args[1]), slPart(args[2]));
  }

  return parseViaCanvas(s) || [0, 0, 0];
}

/**
 * One animation channel.
 *
 * Values arrive as quantised integer deltas along time, so decoding is a
 * running sum per component -- accumulated in integers and divided once, or
 * float error compounds along the timeline. A dense track carries no times at
 * all and is keyed against the document's uniform sample grid, which makes its
 * lookup arithmetic rather than a search.
 */
class Track {
  constructor(raw, stride, duration, samples) {
    this.stride = stride;
    if (!raw) { this.constant = true; this.value = null; return; }

    if (raw.c !== undefined) {
      this.constant = true;
      this.value = stride === 1 ? raw.c : Float32Array.from(raw.c);
      return;
    }

    const q = raw.q || 1;
    const d = raw.d;
    const count = raw.t ? raw.t.length : samples;
    this.constant = false;
    this.values = new Float32Array(count * stride);

    const acc = new Int32Array(stride);
    for (let i = 0; i < count; i++) {
      for (let j = 0; j < stride; j++) {
        const k = i * stride + j;
        acc[j] = i === 0 ? d[k] : acc[j] + d[k];
        this.values[k] = acc[j] / q;
      }
    }

    if (raw.t) {
      this.dense = false;
      this.times = Float32Array.from(raw.t);
      this.cursor = 0;
    } else {
      this.dense = true;
      this.count = count;
      this.step = count > 1 ? duration / (count - 1) : 1;
    }
  }

  /** Cursor-cached segment search: playback is monotonic, so this is O(1)
   *  in the common case and only scans on a seek. */
  seg(t) {
    const times = this.times, n = times.length, c = this.cursor;
    if (t <= times[0]) { this.cursor = 0; return -1; }
    if (t >= times[n - 1]) { this.cursor = n - 2; return n - 1; }
    if (c < n - 1 && t >= times[c] && t <= times[c + 1]) return c;
    if (c + 2 < n && t >= times[c + 1] && t <= times[c + 2]) return (this.cursor = c + 1);
    let lo = 0, hi = n - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (times[mid] <= t) lo = mid; else hi = mid;
    }
    return (this.cursor = lo);
  }

  scalar(t) {
    if (this.constant) return this.value;
    const v = this.values;
    if (this.dense) {
      const x = t / this.step;
      if (!(x > 0)) return v[0];
      if (x >= this.count - 1) return v[this.count - 1];
      const i = x | 0, u = x - i;
      return v[i] + (v[i + 1] - v[i]) * u;
    }
    const times = this.times;
    const i = this.seg(t);
    if (i < 0) return v[0];
    if (i >= times.length - 1) return v[times.length - 1];
    const t0 = times[i], t1 = times[i + 1];
    const u = t1 > t0 ? (t - t0) / (t1 - t0) : 0;
    return v[i] + (v[i + 1] - v[i]) * u;
  }

  /** Interpolate a point set straight into a shared scratch buffer. */
  points(t, out, offset) {
    const s = this.stride, v = this.values;
    let k, a, c, u, i;
    if (this.constant) {
      for (k = 0; k < s; k++) out[offset + k] = this.value[k];
      return;
    }
    if (this.dense) {
      const x = t / this.step;
      if (!(x > 0)) { for (k = 0; k < s; k++) out[offset + k] = v[k]; return; }
      if (x >= this.count - 1) {
        a = (this.count - 1) * s;
        for (k = 0; k < s; k++) out[offset + k] = v[a + k];
        return;
      }
      i = x | 0; u = x - i;
      a = i * s; c = a + s;
      for (k = 0; k < s; k++) out[offset + k] = v[a + k] + (v[c + k] - v[a + k]) * u;
      return;
    }
    const times = this.times, n = times.length;
    i = this.seg(t);
    if (i < 0) { for (k = 0; k < s; k++) out[offset + k] = v[k]; return; }
    if (i >= n - 1) { a = (n - 1) * s; for (k = 0; k < s; k++) out[offset + k] = v[a + k]; return; }
    const t0 = times[i], t1 = times[i + 1];
    u = t1 > t0 ? (t - t0) / (t1 - t0) : 0;
    a = i * s; c = a + s;
    for (k = 0; k < s; k++) out[offset + k] = v[a + k] + (v[c + k] - v[a + k]) * u;
  }
}

export class LerpaPlayer {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {object} doc parsed .lerpa (or bare vecbake) document
   * @param {object} [options]
   */
  constructor(canvas, doc, options = {}) {
    if (!doc || (doc.format !== 'lerpa' && doc.format !== 'vecbake')) {
      throw new Error('Lerpa: not an .lerpa document');
    }
    // A *detached* player (canvas === null) owns no surface. It exists only to
    // be drawn through `drawInto`, which is how you put many instances of one
    // animation onto a single shared canvas. See `drawInto` for the why.
    this.canvas = canvas || null;
    this.ctx = canvas ? canvas.getContext('2d') : null;
    this.doc = doc;
    this.width = doc.w;
    this.height = doc.h;
    this.duration = doc.dur;
    this.fps = doc.fps;
    this.groups = doc.groups || [];
    this.states = doc.states || null;

    this.background = options.background !== undefined ? options.background : (doc.bg || null);
    this.fit = options.fit || 'contain';
    this.speed = options.speed || 1;
    this.lineScale = options.lineScale || 1;
    this.seamFix = options.seamFix !== false;
    this.seamWidth = options.seamWidth || 0.7;

    this.time = 0;
    this.playing = false;
    this.direction = 1;
    this.lastRenderMs = 0;
    this.onend = options.onend || null;

    this.palette = (doc.colors || []).map(parseColor);
    this.overrides = new Array(this.palette.length).fill(null);
    this.elementColors = new Map();
    this.groupColors = new Map();

    const els = doc.els, n = els.length, samples = doc.samples || 0;
    let total = 0;
    for (let i = 0; i < n; i++) total += els[i].n * 2;
    this.scratch = new Float32Array(total);
    this.depths = new Float64Array(n);
    this.order = new Int32Array(n);
    this.visible = 0;
    this.elements = new Array(n);

    let offset = 0;
    for (let i = 0; i < n; i++) {
      const e = els[i];
      this.elements[i] = {
        id: e.id || null,
        kind: e.k,
        npts: e.n,
        group: this.groups[e.g] || '',
        color: e.c,
        width: e.w === undefined ? 1 : e.w,
        geo: new Track(e.G, e.n * 2, this.duration, samples),
        opacity: e.O ? new Track(e.O, 1, this.duration, samples) : null,
        depth: e.D ? new Track(e.D, 1, this.duration, samples) : null,
        shade: e.S ? new Track(e.S, 1, this.duration, samples) : null,
        smooth: decodeSmooth(e.sm, e.n),
        // fills always close; a stroke only when it chained into a ring
        closed: e.k !== STROKE || e.cl === 1,
        offset,
        alpha: 0,
        css: null,
        cssKey: -1,
      };
      offset += e.n * 2;
    }

    this._sorter = (a, b) => this.depths[b] - this.depths[a];
    this._tick = this._tick.bind(this);
    this.resize();
    if (this.canvas && typeof ResizeObserver !== 'undefined' && options.autoResize !== false) {
      this._ro = new ResizeObserver(() => this.resize());
      this._ro.observe(canvas);
    }
    this.seek(0);
  }

  resize() {
    // A detached player has nothing to fit itself to: author space *is* the
    // drawing space, and the caller places it via drawInto's transform.
    if (!this.canvas) {
      this.dpr = 1;
      this.scaleX = this.scaleY = 1;
      this.offX = this.offY = 0;
      return;
    }
    const dpr = (typeof window !== 'undefined' && window.devicePixelRatio) || 1;
    const rect = this.canvas.getBoundingClientRect();
    const cw = Math.max(1, Math.round(rect.width || this.width));
    const ch = Math.max(1, Math.round(rect.height || this.height));
    this.canvas.width = Math.round(cw * dpr);
    this.canvas.height = Math.round(ch * dpr);
    const sx = cw / this.width, sy = ch / this.height;
    if (this.fit === 'stretch') {
      this.scaleX = sx; this.scaleY = sy; this.offX = 0; this.offY = 0;
    } else {
      const s = this.fit === 'cover' ? Math.max(sx, sy) : Math.min(sx, sy);
      this.scaleX = this.scaleY = s;
      this.offX = (cw - this.width * s) / 2;
      this.offY = (ch - this.height * s) / 2;
    }
    this.dpr = dpr;
    this.render(this.time);
  }

  _rgb(el) {
    if (el.id && this.elementColors.has(el.id)) return this.elementColors.get(el.id);
    if (this.groupColors.has(el.group)) return this.groupColors.get(el.group);
    return this.overrides[el.color] || this.palette[el.color] || [0, 0, 0];
  }

  _css(el, shade) {
    // colour strings are the only per-frame allocation, so memoise against a
    // quantised shade
    const q = (shade * 64) | 0;
    if (el.cssKey === q && el.css) return el.css;
    const c = this._rgb(el);
    el.cssKey = q;
    el.css = `rgb(${(c[0] * shade) | 0},${(c[1] * shade) | 0},${(c[2] * shade) | 0})`;
    return el.css;
  }

  /**
   * Trace one element into the current path: straight through corners,
   * Catmull-Rom cubics through smooth vertices. A corner carries a zero
   * tangent, which collapses its cubic to exactly the straight segment it
   * replaced -- so the two mix inside one path with no special case at the
   * join, and an element with no mask costs nothing extra.
   *
   * The same formula lives in the add-on's curves.py and emit_svg.py. Change
   * one and the animation stops matching its own resting states.
   */
  _trace(ctx, s, el, closed) {
    const o = el.offset, n = el.npts, m = el.smooth;
    ctx.moveTo(s[o], s[o + 1]);
    if (!m) {
      for (let p = 1; p < n; p++) ctx.lineTo(s[o + p * 2], s[o + p * 2 + 1]);
      return;
    }
    let tix = 0, tiy = 0;
    if (m[0] && closed) {
      tix = (s[o + 2] - s[o + (n - 1) * 2]) * 0.5;
      tiy = (s[o + 3] - s[o + (n - 1) * 2 + 1]) * 0.5;
    }
    const last = closed ? n : n - 1;
    for (let i = 0; i < last; i++) {
      const j = (i + 1) % n;
      let tjx = 0, tjy = 0;
      if (m[j] && (closed || (j > 0 && j < n - 1))) {
        const a = closed ? (j - 1 + n) % n : j - 1;
        const b = closed ? (j + 1) % n : j + 1;
        tjx = (s[o + b * 2] - s[o + a * 2]) * 0.5;
        tjy = (s[o + b * 2 + 1] - s[o + a * 2 + 1]) * 0.5;
      }
      const xj = s[o + j * 2], yj = s[o + j * 2 + 1];
      if (tix === 0 && tiy === 0 && tjx === 0 && tjy === 0) {
        ctx.lineTo(xj, yj);
      } else {
        const xi = s[o + i * 2], yi = s[o + i * 2 + 1];
        ctx.bezierCurveTo(xi + tix / 3, yi + tiy / 3, xj - tjx / 3, yj - tjy / 3, xj, yj);
      }
      tix = tjx; tiy = tjy;
    }
  }

  /**
   * Evaluate every element at time `t` and sort the survivors back to front.
   *
   * Leaves point positions in `this.scratch` and the draw order in the first
   * `count` slots of `this.order`, and returns that count. Split out of
   * `render` so `drawInto` can reuse it.
   */
  _solve(t) {
    const els = this.elements, n = els.length;
    const scratch = this.scratch, depths = this.depths, order = this.order;
    let count = 0, i, el;

    for (i = 0; i < n; i++) {
      el = els[i];
      const a = el.opacity ? el.opacity.scalar(t) : 1;
      if (a <= 0.004) continue;
      el.alpha = a > 1 ? 1 : a;
      el.geo.points(t, scratch, el.offset);
      depths[i] = el.depth ? el.depth.scalar(t) : 0;
      order[count++] = i;
    }
    this.visible = count;

    // subarray shares memory with `order`, so this sorts it in place
    Array.prototype.sort.call(order.subarray(0, count), this._sorter);   // far -> near
    return count;
  }

  /** Paint `count` already-solved elements under whatever transform ctx carries. */
  _paint(ctx, t, count) {
    const els = this.elements, scratch = this.scratch, order = this.order;
    let el, j = 0;
    while (j < count) {
      el = els[order[j]];
      if (el.kind === STROKE) {
        // batch the run of identically styled strokes into one path
        const css = this._css(el, 1), width = el.width, alpha = el.alpha;
        ctx.beginPath();
        let k = j;
        while (k < count) {
          const e2 = els[order[k]];
          if (e2.kind !== STROKE || e2.width !== width || e2.alpha !== alpha ||
              this._css(e2, 1) !== css) break;
          this._trace(ctx, scratch, e2, e2.closed);
          if (e2.closed) ctx.closePath();   // ends this subpath, not the batch
          k++;
        }
        ctx.globalAlpha = alpha;
        ctx.strokeStyle = css;
        ctx.lineWidth = width * this.lineScale;
        ctx.stroke();
        j = k;
      } else {
        const shade = el.shade ? el.shade.scalar(t) : 1;
        ctx.globalAlpha = el.alpha;
        ctx.fillStyle = this._css(el, shade);
        ctx.beginPath();
        this._trace(ctx, scratch, el, true);
        ctx.closePath();
        ctx.fill();
        // Not on a translucent fill: globalAlpha applies per draw call, so a
        // stroke over its own fill blends twice along the boundary and rings
        // the shape in a darker outline. emit_svg.py skips it too, to match.
        if (this.seamFix && el.alpha > 0.999) {
          // abutting polygons leave an antialiased hairline in every 2D
          // rasteriser; stroking each fill with its own colour closes it
          ctx.strokeStyle = ctx.fillStyle;
          ctx.lineWidth = this.seamWidth;
          ctx.stroke();
        }
        j++;
      }
    }
  }

  /**
   * Draw one frame into a context the caller owns -- *without* clearing it.
   *
   * `render` wipes its whole canvas before drawing, which is correct when the
   * player owns that canvas and wrong the moment two of them share one: the
   * second wipes away the first. `drawInto` is the sharing-friendly half. Wipe
   * once yourself, then draw as many instances as you like onto the same
   * surface. That keeps the browser compositing a single layer instead of one
   * per instance, which is what actually costs you at a hundred of them.
   *
   * Instances are still separate players over one shared, read-only document,
   * so each keeps its own clock and its own `setGroupColor`. Note they do each
   * decode their own copy of the animation tracks -- a few KB for something
   * small, but not something to do a hundred times with a heavy scene.
   *
   * @param {CanvasRenderingContext2D} ctx destination, transform already set
   * @param {number} t time in seconds
   * @param {object} [transform] convenience placement, applied on top of ctx's
   *   own transform. `x`,`y` position the anchor; `scale` (or `scaleX`/`scaleY`)
   *   sizes it; `rotation` is radians clockwise about the anchor; `anchor` is
   *   `'center'` (default) or `'topleft'`.
   *
   * @example
   * ctx.clearRect(0, 0, w, h);
   * for (const b of butterflies) {
   *   b.player.drawInto(ctx, b.t, { x: b.x, y: b.y, rotation: b.heading, scale: 0.4 });
   * }
   */
  drawInto(ctx, t, transform) {
    const count = this._solve(t);
    // save/restore so we cannot leak our fillStyle, alpha or transform back to
    // the caller -- they are drawing other things onto this same context
    ctx.save();
    if (transform) {
      const x = transform.x || 0, y = transform.y || 0;
      const rotation = transform.rotation || 0;
      const s = transform.scale === undefined ? 1 : transform.scale;
      const sx = transform.scaleX === undefined ? s : transform.scaleX;
      const sy = transform.scaleY === undefined ? s : transform.scaleY;
      ctx.translate(x, y);
      if (rotation) ctx.rotate(rotation);
      if (sx !== 1 || sy !== 1) ctx.scale(sx, sy);
      // Author space runs 0..w, 0..h from the top-left, so centring means
      // stepping back by half the artwork *after* rotating and scaling --
      // otherwise the drawing swings around its own corner.
      if (transform.anchor !== 'topleft') ctx.translate(-this.width / 2, -this.height / 2);
    }
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    this._paint(ctx, t, count);
    ctx.restore();
  }

  render(t) {
    if (!this.ctx) return;            // detached: drawInto is the only way in
    const started = performance.now();
    const ctx = this.ctx;

    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    const w = this.canvas.width / this.dpr, h = this.canvas.height / this.dpr;
    // Always clear first. Painting the background over the previous frame is
    // not enough: any colour with alpha -- including the literal 'transparent'
    // -- composites as a no-op in source-over, so nothing gets erased and every
    // frame smears on top of the last.
    ctx.clearRect(0, 0, w, h);
    if (this.background) { ctx.fillStyle = this.background; ctx.fillRect(0, 0, w, h); }
    ctx.setTransform(this.scaleX * this.dpr, 0, 0, this.scaleY * this.dpr,
                     this.offX * this.dpr, this.offY * this.dpr);

    this.drawInto(ctx, t);
    this.lastRenderMs = performance.now() - started;
  }

  seek(t) {
    this.time = Math.max(0, Math.min(this.duration, t));
    this.render(this.time);
    return this;
  }

  /** @param {{loop?:boolean, from?:number, reverse?:boolean}} [options] */
  play(options = {}) {
    this.loop = !!options.loop;
    this.direction = options.reverse ? -1 : 1;
    if (options.from !== undefined) this.time = options.from;
    else if (this.direction < 0 && this.time <= 0) this.time = this.duration;
    else if (this.direction > 0 && this.time >= this.duration) this.time = 0;
    if (this.playing) return this;
    this.playing = true;
    // Paint the starting frame now rather than leaving it to the first tick.
    // requestAnimationFrame does not run until the next frame, so whatever was
    // on the canvas stays up for one frame -- blank on a fresh player, and for
    // a reverse it is frame 0, the *wrong end* of the clip, because the jump to
    // `duration` above only moves the clock.
    this.render(this.time);
    this._last = null;
    this._raf = requestAnimationFrame(this._tick);
    return this;
  }

  /** Play backwards. No second file needed -- reversing is a time transform. */
  reverse(options = {}) {
    return this.play({ ...options, reverse: true });
  }

  pause() {
    this.playing = false;
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = null;
    return this;
  }

  _tick(now) {
    if (!this.playing) return;
    if (this._last === null) this._last = now;
    const dt = (now - this._last) / 1000 * this.speed * this.direction;
    this._last = now;
    let t = this.time + dt;
    const done = this.direction > 0 ? t >= this.duration : t <= 0;
    if (done) {
      if (this.loop) {
        t = this.direction > 0 ? t % this.duration : this.duration + (t % this.duration);
      } else {
        this.seek(this.direction > 0 ? this.duration : 0);
        this.pause();
        if (this.onend) this.onend();
        return;
      }
    }
    this.seek(t);
    this._raf = requestAnimationFrame(this._tick);
  }

  // --- theming and live data binding ---------------------------------------

  _invalidate() {
    for (const el of this.elements) el.cssKey = -1;
    this.render(this.time);
  }
  /** Recolour every element sharing a palette swatch. */
  setColor(index, css) {
    this.overrides[index] = css ? parseColor(css) : null;
    this._invalidate();
    return this;
  }
  /** Recolour a whole named group -- the usual hook for live data. */
  setGroupColor(group, css) {
    if (css) this.groupColors.set(group, parseColor(css));
    else this.groupColors.delete(group);
    this._invalidate();
    return this;
  }
  setElementColor(id, css) {
    if (css) this.elementColors.set(id, parseColor(css));
    else this.elementColors.delete(id);
    this._invalidate();
    return this;
  }

  /** Hit-test in client coordinates. Works during playback, because elements
   *  stay real objects rather than pixels. */
  pick(clientX, clientY, slop = 6) {
    const rect = this.canvas.getBoundingClientRect();
    const x = (clientX - rect.left - this.offX) / this.scaleX;
    const y = (clientY - rect.top - this.offY) / this.scaleY;
    const ctx = this.ctx, scratch = this.scratch;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    const view = this.order.subarray(0, this.visible);
    for (let i = view.length - 1; i >= 0; i--) {
      const el = this.elements[view[i]];
      ctx.beginPath();
      // trace the curve, not the polyline, or hit testing drifts off the
      // shape the viewer can actually see
      this._trace(ctx, scratch, el, el.closed);
      if (el.kind === STROKE) {
        if (el.closed) ctx.closePath();
        ctx.lineWidth = Math.max(el.width, slop);
        if (ctx.isPointInStroke(x, y)) return { id: el.id, group: el.group, index: view[i] };
      } else {
        ctx.closePath();
        if (ctx.isPointInPath(x, y)) return { id: el.id, group: el.group, index: view[i] };
      }
    }
    return null;
  }

  /** The resting state either side of the transition, as an SVG string.
   *  @param {'start'|'end'} which */
  state(which = 'end') {
    return this.states ? this.states[which] || null : null;
  }

  destroy() {
    this.pause();
    if (this._ro) this._ro.disconnect();
  }
}

const DATA_ID = 'lerpa-data';

/**
 * Parse an .lerpa container, or a bare track JSON.
 *
 * An .lerpa is a valid SVG carrying its animation in a <metadata> block, so the
 * same file renders as a picture and drives an animation. Both resting states
 * ride along inside it and are lifted back out here as standalone SVG strings.
 */
/**
 * Per-vertex smooth flags: 1 means every vertex, a hex string is a bitmask
 * whose bit i is vertex i, anything else means none. Null for a plain
 * polyline so the draw loop can skip the curve path entirely.
 */
function decodeSmooth(sm, n) {
  if (sm === 1) { const m = new Uint8Array(n); m.fill(1); return m; }
  if (typeof sm !== 'string' || !sm) return null;
  const m = new Uint8Array(n);
  const last = sm.length - 1;
  for (let i = 0; i < n; i++) {
    const ch = sm[last - (i >> 2)];              // last digit holds vertices 0-3
    if (ch === undefined) break;
    m[i] = (parseInt(ch, 16) >> (i & 3)) & 1;
  }
  return m;
}

export function parseDocument(text) {
  const trimmed = text.trimStart();
  if (trimmed[0] === '{') return JSON.parse(text);          // bare track JSON
  if (trimmed[0] !== '<') throw new Error('Lerpa: unrecognised file');

  let doc, states = null;
  if (typeof DOMParser !== 'undefined') {
    const xml = new DOMParser().parseFromString(text, 'image/svg+xml');
    const node = xml.getElementById(DATA_ID);
    if (!node) throw new Error('Lerpa: SVG has no embedded animation data');
    doc = JSON.parse(node.textContent);
    const root = xml.documentElement;
    const header = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="${root.getAttribute('viewBox')}"`
      + ` width="${root.getAttribute('width')}" height="${root.getAttribute('height')}">`;
    states = {};
    for (const which of ['start', 'end']) {
      const g = xml.getElementById(`lerpa-state-${which}`);
      if (g) states[which] = `${header}${g.innerHTML}</svg>`;
    }
  } else {
    // no DOM (Node, a build step): pull the payload out textually
    const m = text.match(/<metadata[^>]*id="lerpa-data"[^>]*>([\s\S]*?)<\/metadata>/);
    if (!m) throw new Error('Lerpa: SVG has no embedded animation data');
    doc = JSON.parse(m[1].replace(/<!\[CDATA\[/g, '').replace(/\]\]>/g, ''));
  }
  if (states) doc.states = states;
  return doc;
}

/** Fetch and parse an .lerpa document. */
export async function fetchDocument(src, init) {
  const res = await fetch(src, init);
  if (!res.ok) throw new Error(`Lerpa: ${res.status} ${res.statusText} loading ${src}`);
  // read as text, not res.json(): a static host will usually guess the wrong
  // MIME type for this extension, and the container is XML anyway
  return parseDocument(await res.text());
}
