# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""Encode a folder of numbered PNGs to an H.264 MP4 with Blender's own FFmpeg.

    blender -b --factory-startup --python scripts/encode_frames.py -- <frames_dir> <out.mp4> [fps]

For a machine with no ffmpeg on it. The sequencer does no colour work beyond the
Standard view, which is what keeps the page's colours the page's colours.
"""
import os
import sys

import bpy

args = sys.argv[sys.argv.index("--") + 1:]
frames_dir, out = os.path.abspath(args[0]), os.path.abspath(args[1])
fps = int(args[2]) if len(args) > 2 else 30
names = sorted(n for n in os.listdir(frames_dir) if n.endswith(".png"))
first = bpy.data.images.load(os.path.join(frames_dir, names[0]))
w, h = first.size
scene = bpy.context.scene
scene.render.resolution_x, scene.render.resolution_y = w, h
scene.render.resolution_percentage = 100
scene.render.fps = fps
scene.frame_start, scene.frame_end = 1, len(names)
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"
se = scene.sequence_editor_create()
strip = se.strips.new_image("frames", os.path.join(frames_dir, names[0]), 1, 1)
for n in names[1:]:
    strip.elements.append(n)
strip.frame_final_duration = len(names)
r = scene.render
r.use_sequencer = True
r.use_compositing = False
s = r.image_settings
s.media_type = "VIDEO"
s.file_format = "FFMPEG"
s.color_mode = "RGB"
r.ffmpeg.format = "MPEG4"
r.ffmpeg.codec = "H264"
r.ffmpeg.constant_rate_factor = "HIGH"
r.ffmpeg.ffmpeg_preset = "BEST"
r.ffmpeg.gopsize = fps
r.ffmpeg.audio_codec = "NONE"
r.filepath = out
print(f"[encode] {len(names)} frames {w}x{h} at {fps} fps -> {out}", flush=True)
bpy.ops.render.render(animation=True)
print("[encode] done", os.path.getsize(r.frame_path()) if os.path.exists(r.frame_path()) else "?", flush=True)
