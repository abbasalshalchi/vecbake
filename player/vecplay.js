/**
 * VecPlay -- tiny Canvas 2D player for VecBake animations.
 *
 * No dependencies. The runtime does three things per frame: interpolate sparse
 * 2D keyframes, painter-sort by interpolated depth, draw. That middle step is
 * the reason this exists rather than Lottie or SMIL -- paint order is
 * recomputed every frame, so occlusion can legitimately flip mid-animation.
 *
 *   const p = await VecPlay.load(canvas, 'tower.json');
 *   p.play({ loop: true });
 *   p.setGroupColor('Cabinets', '#e5484d');   // live data binding
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.VecPlay = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var STROKE = 0;

  function hexToRgb(hex) {
    var n = parseInt(hex.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }

  function parseColor(css) {
    if (css[0] === '#') {
      if (css.length === 4) {
        return [parseInt(css[1] + css[1], 16), parseInt(css[2] + css[2], 16), parseInt(css[3] + css[3], 16)];
      }
      return hexToRgb(css);
    }
    var m = css.match(/(\d+(\.\d+)?)/g);
    return m ? [+m[0], +m[1], +m[2]] : [0, 0, 0];
  }

  /** Track -> typed arrays. Handles constant, sparse and dense encodings.
   *
   *  Values arrive as quantised integer deltas along time, so decoding is a
   *  running sum per component. Accumulate in integers and divide once, or
   *  float error compounds across the whole timeline. A dense track carries no
   *  times at all -- it is keyed against the document's uniform sample grid.
   */
  function Track(raw, stride, duration, samples) {
    this.stride = stride;
    if (!raw) { this.constant = true; this.value = null; return; }

    if (raw.c !== undefined) {
      this.constant = true;
      this.value = stride === 1 ? raw.c : Float32Array.from(raw.c);
      return;
    }

    var q = raw.q || 1, d = raw.d, i, j;
    var count = raw.t ? raw.t.length : samples;
    this.constant = false;
    this.values = new Float32Array(count * stride);

    var acc = new Int32Array(stride);
    for (i = 0; i < count; i++) {
      for (j = 0; j < stride; j++) {
        var k = i * stride + j;
        acc[j] = i === 0 ? d[k] : acc[j] + d[k];
        this.values[k] = acc[j] / q;
      }
    }

    if (raw.t) {
      this.dense = false;
      this.times = Float32Array.from(raw.t);
    } else {
      // implicit uniform timeline: lookup is arithmetic, not a search
      this.dense = true;
      this.count = count;
      this.step = count > 1 ? duration / (count - 1) : 1;
    }
    this.cursor = 0;
  }

  // Dense lookup is inlined at both call sites below rather than returning a
  // pair. Returning [i, u] allocates once per track per frame -- thousands of
  // short-lived arrays a frame, which costs more in GC than the whole draw.

  /** Locate the segment containing t. Cursor-cached: playback is monotonic, so
   *  this is O(1) in the common case and only falls back to a scan on seeks. */
  Track.prototype.seg = function (t) {
    var times = this.times, n = times.length, c = this.cursor;
    if (t <= times[0]) return (this.cursor = 0), -1;
    if (t >= times[n - 1]) return (this.cursor = n - 2), n - 1;
    if (c < n - 1 && t >= times[c] && t <= times[c + 1]) return c;
    if (c + 2 < n && t >= times[c + 1] && t <= times[c + 2]) return (this.cursor = c + 1);
    var lo = 0, hi = n - 1;
    while (hi - lo > 1) {
      var mid = (lo + hi) >> 1;
      if (times[mid] <= t) lo = mid; else hi = mid;
    }
    return (this.cursor = lo);
  };

  Track.prototype.scalar = function (t) {
    if (this.constant) return this.value;
    var v = this.values, i, u;
    if (this.dense) {
      var x = t / this.step;
      if (!(x > 0)) return v[0];
      if (x >= this.count - 1) return v[this.count - 1];
      i = x | 0; u = x - i;
      return v[i] + (v[i + 1] - v[i]) * u;
    }
    var times = this.times;
    i = this.seg(t);
    if (i < 0) return v[0];
    if (i >= times.length - 1) return v[times.length - 1];
    var t0 = times[i], t1 = times[i + 1];
    u = t1 > t0 ? (t - t0) / (t1 - t0) : 0;
    return v[i] + (v[i + 1] - v[i]) * u;
  };

  /** Interpolate a point set straight into the shared scratch buffer. */
  Track.prototype.points = function (t, out, offset) {
    var s = this.stride, k, v = this.values, i, u, a, c;
    if (this.constant) {
      for (k = 0; k < s; k++) out[offset + k] = this.value[k];
      return;
    }
    if (this.dense) {
      var x = t / this.step;
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
    var times = this.times, n = times.length;
    i = this.seg(t);
    if (i < 0) { for (k = 0; k < s; k++) out[offset + k] = v[k]; return; }
    if (i >= n - 1) { var b = (n - 1) * s; for (k = 0; k < s; k++) out[offset + k] = v[b + k]; return; }
    var t0 = times[i], t1 = times[i + 1];
    u = t1 > t0 ? (t - t0) / (t1 - t0) : 0;
    a = i * s; c = a + s;
    for (k = 0; k < s; k++) out[offset + k] = v[a + k] + (v[c + k] - v[a + k]) * u;
  };

  function VecPlay(canvas, data, opts) {
    opts = opts || {};
    if (!data || data.format !== 'vecbake') throw new Error('VecPlay: not a vecbake document');
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.data = data;
    this.width = data.w;
    this.height = data.h;
    this.duration = data.dur;
    this.fps = data.fps;
    this.groups = data.groups || [];
    this.background = opts.background !== undefined ? opts.background : (data.bg || null);
    this.lineScale = opts.lineScale || 1;
    // Abutting polygons leave an antialiased hairline gap in every 2D
    // rasteriser. Stroking each fill with its own colour closes it. The SVG
    // emitter does the same thing so the two stay identical.
    this.seamFix = opts.seamFix !== false;
    this.seamWidth = opts.seamWidth || 0.7;
    this.fit = opts.fit || 'contain';
    this.speed = opts.speed || 1;
    this.time = 0;
    this.playing = false;
    this.lastRenderMs = 0;
    this.onend = opts.onend || null;

    this.palette = (data.colors || []).map(parseColor);
    this.overrides = new Array(this.palette.length).fill(null);
    this.elementColors = new Map();
    this.groupColors = new Map();

    var samples = data.samples || 0;
    var els = data.els, n = els.length, total = 0, i;
    this.elements = new Array(n);
    for (i = 0; i < n; i++) total += els[i].n * 2;
    this.scratch = new Float32Array(total);
    this.depths = new Float64Array(n);
    this.order = new Int32Array(n);
    this.visible = 0;

    var offset = 0;
    for (i = 0; i < n; i++) {
      var e = els[i];
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
        offset: offset,
        alpha: 0,
        css: null,
        cssKey: -1
      };
      offset += e.n * 2;
    }

    this._sorter = function (a, b) { return this.depths[b] - this.depths[a]; }.bind(this);
    this._tick = this._tick.bind(this);
    this.resize();
    if (typeof ResizeObserver !== 'undefined' && opts.autoResize !== false) {
      this._ro = new ResizeObserver(this.resize.bind(this));
      this._ro.observe(canvas);
    }
    this.seek(0);
  }

  VecPlay.load = function (canvas, url, opts) {
    return fetch(url).then(function (r) { return r.json(); }).then(function (doc) {
      return new VecPlay(canvas, doc, opts);
    });
  };

  VecPlay.prototype.resize = function () {
    var dpr = window.devicePixelRatio || 1;
    var rect = this.canvas.getBoundingClientRect();
    var cw = Math.max(1, Math.round(rect.width || this.width));
    var ch = Math.max(1, Math.round(rect.height || this.height));
    this.canvas.width = Math.round(cw * dpr);
    this.canvas.height = Math.round(ch * dpr);
    var sx = cw / this.width, sy = ch / this.height;
    var s = this.fit === 'stretch' ? null : (this.fit === 'cover' ? Math.max(sx, sy) : Math.min(sx, sy));
    if (s === null) { this.scaleX = sx; this.scaleY = sy; this.offX = 0; this.offY = 0; }
    else {
      this.scaleX = this.scaleY = s;
      this.offX = (cw - this.width * s) / 2;
      this.offY = (ch - this.height * s) / 2;
    }
    this.dpr = dpr;
    this.render(this.time);
  };

  /** Resolve an element's colour: element override > group override > palette. */
  VecPlay.prototype._rgb = function (el) {
    if (el.id && this.elementColors.has(el.id)) return this.elementColors.get(el.id);
    if (this.groupColors.has(el.group)) return this.groupColors.get(el.group);
    return this.overrides[el.color] || this.palette[el.color] || [0, 0, 0];
  };

  VecPlay.prototype._css = function (el, shade) {
    // Colour strings are the one real allocation per frame, so memoise them
    // against a quantised shade -- shade tracks barely move between frames.
    var q = (shade * 64) | 0;
    if (el.cssKey === q && el.css) return el.css;
    var c = this._rgb(el);
    el.cssKey = q;
    el.css = 'rgb(' + ((c[0] * shade) | 0) + ',' + ((c[1] * shade) | 0) + ',' + ((c[2] * shade) | 0) + ')';
    return el.css;
  };

  VecPlay.prototype.render = function (t) {
    var t0 = (typeof performance !== 'undefined' ? performance.now() : 0);
    var ctx = this.ctx, els = this.elements, n = els.length;
    var scratch = this.scratch, depths = this.depths, order = this.order;
    var count = 0, i, el;

    for (i = 0; i < n; i++) {
      el = els[i];
      var a = el.opacity ? el.opacity.scalar(t) : 1;
      if (a <= 0.004) continue;
      el.alpha = a > 1 ? 1 : a;
      el.geo.points(t, scratch, el.offset);
      depths[i] = el.depth ? el.depth.scalar(t) : 0;
      order[count++] = i;
    }
    this.visible = count;

    var view = order.subarray(0, count);
    Array.prototype.sort.call(view, this._sorter);   // far -> near

    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    if (this.background) {
      ctx.fillStyle = this.background;
      ctx.fillRect(0, 0, this.canvas.width / this.dpr, this.canvas.height / this.dpr);
    } else {
      ctx.clearRect(0, 0, this.canvas.width / this.dpr, this.canvas.height / this.dpr);
    }
    ctx.setTransform(this.scaleX * this.dpr, 0, 0, this.scaleY * this.dpr,
                     this.offX * this.dpr, this.offY * this.dpr);
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    var j = 0;
    while (j < count) {
      el = els[view[j]];
      if (el.kind === STROKE) {
        // Batch the run of identically-styled strokes that follows into one
        // path. A lattice mast is hundreds of these, so it matters.
        var css = this._css(el, 1), width = el.width, alpha = el.alpha;
        ctx.beginPath();
        var k = j;
        while (k < count) {
          var e2 = els[view[k]];
          if (e2.kind !== STROKE || e2.width !== width || e2.alpha !== alpha ||
              this._css(e2, 1) !== css) break;
          var o = e2.offset, np = e2.npts;
          ctx.moveTo(scratch[o], scratch[o + 1]);
          for (var p = 1; p < np; p++) ctx.lineTo(scratch[o + p * 2], scratch[o + p * 2 + 1]);
          k++;
        }
        ctx.globalAlpha = alpha;
        ctx.strokeStyle = css;
        ctx.lineWidth = width * this.lineScale;
        ctx.stroke();
        j = k;
      } else {
        var shade = el.shade ? el.shade.scalar(t) : 1;
        ctx.globalAlpha = el.alpha;
        ctx.fillStyle = this._css(el, shade);
        ctx.beginPath();
        var off = el.offset, npts = el.npts;
        ctx.moveTo(scratch[off], scratch[off + 1]);
        for (var q = 1; q < npts; q++) ctx.lineTo(scratch[off + q * 2], scratch[off + q * 2 + 1]);
        ctx.closePath();
        ctx.fill();
        if (this.seamFix) {
          ctx.strokeStyle = ctx.fillStyle;
          ctx.lineWidth = this.seamWidth;
          ctx.stroke();
        }
        j++;
      }
    }
    ctx.globalAlpha = 1;
    this.lastRenderMs = (typeof performance !== 'undefined' ? performance.now() : 0) - t0;
  };

  VecPlay.prototype.seek = function (t) {
    this.time = Math.max(0, Math.min(this.duration, t));
    this.render(this.time);
    return this;
  };

  VecPlay.prototype.play = function (opts) {
    opts = opts || {};
    this.loop = !!opts.loop;
    if (opts.from !== undefined) this.time = opts.from;
    if (this.time >= this.duration) this.time = 0;
    if (this.playing) return this;
    this.playing = true;
    this._last = null;
    this._raf = requestAnimationFrame(this._tick);
    return this;
  };

  VecPlay.prototype.pause = function () {
    this.playing = false;
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = null;
    return this;
  };

  VecPlay.prototype._tick = function (now) {
    if (!this.playing) return;
    if (this._last === null) this._last = now;
    var dt = (now - this._last) / 1000 * this.speed;
    this._last = now;
    var t = this.time + dt;
    if (t >= this.duration) {
      if (this.loop) t = t % this.duration;
      else {
        this.seek(this.duration);
        this.pause();
        if (this.onend) this.onend();
        return;
      }
    }
    this.seek(t);
    this._raf = requestAnimationFrame(this._tick);
  };

  // --- theming / data binding ----------------------------------------------

  VecPlay.prototype._invalidate = function () {
    for (var i = 0; i < this.elements.length; i++) this.elements[i].cssKey = -1;
    this.render(this.time);
  };
  VecPlay.prototype.setColor = function (index, css) {
    this.overrides[index] = css ? parseColor(css) : null;
    this._invalidate();
    return this;
  };
  VecPlay.prototype.setGroupColor = function (group, css) {
    if (css) this.groupColors.set(group, parseColor(css));
    else this.groupColors.delete(group);
    this._invalidate();
    return this;
  };
  VecPlay.prototype.setElementColor = function (id, css) {
    if (css) this.elementColors.set(id, parseColor(css));
    else this.elementColors.delete(id);
    this._invalidate();
    return this;
  };

  /** Hit-test in canvas coordinates -- works mid-animation, which is the whole
   *  point of keeping elements as real objects. Near-to-far. */
  VecPlay.prototype.pick = function (clientX, clientY, slop) {
    var rect = this.canvas.getBoundingClientRect();
    var x = (clientX - rect.left - this.offX) / this.scaleX;
    var y = (clientY - rect.top - this.offY) / this.scaleY;
    var ctx = this.ctx, scratch = this.scratch;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    var view = this.order.subarray(0, this.visible);
    for (var i = view.length - 1; i >= 0; i--) {
      var el = this.elements[view[i]];
      ctx.beginPath();
      var o = el.offset;
      ctx.moveTo(scratch[o], scratch[o + 1]);
      for (var p = 1; p < el.npts; p++) ctx.lineTo(scratch[o + p * 2], scratch[o + p * 2 + 1]);
      if (el.kind === STROKE) {
        ctx.lineWidth = Math.max(el.width, slop || 6);
        if (ctx.isPointInStroke(x, y)) return { id: el.id, group: el.group, index: view[i] };
      } else {
        ctx.closePath();
        if (ctx.isPointInPath(x, y)) return { id: el.id, group: el.group, index: view[i] };
      }
    }
    return null;
  };

  VecPlay.prototype.destroy = function () {
    this.pause();
    if (this._ro) this._ro.disconnect();
  };

  VecPlay.version = 1;
  return VecPlay;
});
