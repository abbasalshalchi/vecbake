# SPDX-License-Identifier: GPL-3.0-or-later
# VecBake -- Copyright (C) 2026 Abbas Alshalchi
"""The agent interface over a real connection, in a real window.

    python scripts/agent_connection_check.py <blender.exe> <lab-mcp-dir> <work-dir> [heavy.blend]

Starts its own Blender with a window, runs the Blender Lab MCP add-on's own server in
it on a port of its own, and speaks that server's wire protocol, so the code runs
exactly where an agent's code runs: exec() on a main-thread timer, inside its weak
sandbox. Nothing here touches a Blender already open. Set BLENDER_USER_RESOURCES to a
profile with the add-on installed, and BLENDER_MCP_EXE to the connector's blender-mcp.exe
to go through it as well.

It is not a test_ file because `-b` has no event loop: the timer this exists to prove
would never fire. Exit code 0 is the contract all the same.

Given a .blend it then bakes that in full and reports how long the window went
unanswered. Nothing is saved: the file is copied first.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import time

BLENDER, LAB_MCP, WORK = sys.argv[1:4]
HEAVY = sys.argv[4] if len(sys.argv) > 4 else None
PORT = 9877
FAILS = []

BOOT = f"""
import sys, bpy
sys.path.insert(0, {os.path.dirname(LAB_MCP)!r})
import importlib
pkg = importlib.import_module({os.path.basename(LAB_MCP)!r})
from importlib import import_module
server = import_module(pkg.__name__ + ".mcp_to_blender_server")
interactive = import_module(pkg.__name__ + ".execute_interactive")
server.start("127.0.0.1", {PORT})
bpy.app.timers.register(interactive.run, first_interval=0.05, persistent=True)
print("READY", flush=True)
"""


def check(label, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + label + (f"  {detail}" if detail and not ok else ""), flush=True)
    if not ok:
        FAILS.append(label)


def call(code, timeout=600.0):
    """One request as the bridge sends it. Returns (result, seconds it took to answer)."""
    began = time.perf_counter()
    with socket.create_connection(("127.0.0.1", PORT), timeout=timeout) as conn:
        conn.settimeout(timeout)
        conn.sendall(json.dumps({"type": "execute", "strict_json": False, "code": code}).encode() + b"\0")
        data = b""
        while not data.endswith(b"\0"):
            chunk = conn.recv(1 << 20)
            if not chunk:
                break
            data += chunk
    took = time.perf_counter() - began
    reply = json.loads(data.rstrip(b"\0"))
    if reply.get("status") != "ok":
        raise RuntimeError(reply.get("message") or reply)
    return reply["result"], took


def status():
    got, took = call("import bpy\nresult = dict(bpy.context.window_manager.vecbake.status())")
    return got, took


def wait_done(interval=0.25, limit=3600):
    worst, polls = 0.0, 0
    began = time.perf_counter()
    while True:
        got, took = status()
        worst, polls = max(worst, took), polls + 1
        if got["state"] != "running":
            return got, worst, polls
        if time.perf_counter() - began > limit:
            raise TimeoutError(got)
        time.sleep(interval)


SCENE = """
import bpy
bpy.ops.wm.read_homefile(use_empty=False, use_factory_startup=True)
scene = bpy.context.scene
scene.frame_start, scene.frame_end = 1, 48
scene.frame_set(7)
scene.render.resolution_x, scene.render.resolution_y = 640, 360
bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=4, radius=1.5, location=(0, 0, 2))
cam = scene.camera
cam.keyframe_insert("location", frame=1)
cam.location.x += 2.0
cam.keyframe_insert("location", frame=48)
result = {"objects": len(scene.objects), "frame": scene.frame_current}
"""


def controlled():
    out = os.path.join(WORK, "out").replace("\\", "/")
    call(SCENE)
    info, _ = call("import bpy\nresult = bpy.context.window_manager.vecbake.describe()")
    check("describe() answers over the connection", info["addon"] == "vecbake" and info["job"]["state"] == "idle", info)
    check("describe() sees the scene the connection is in", info["scene"]["frame_current"] == 7, info["scene"])

    started, took = call(f"""
import bpy
result = bpy.context.window_manager.vecbake.start({{"directory": {out!r}, "basename": "shot", "formats": ["lerpa", "json"]}})
""")
    print(f"     start returned in {took * 1000:.0f} ms, state {started['state']}")
    check("start returns while the bake runs", started["state"] == "running", started)
    baking, _ = call("import bpy\nresult = {'baking': bpy.context.scene.vecbake.baking, 'text': bpy.context.scene.vecbake.progress_text}")
    check("the panel shows the bake to whoever is at the machine", baking["baking"] is True, baking)

    done, worst, polls = wait_done()
    print(f"     {polls} polls, slowest answer {worst * 1000:.0f} ms, bake {done['elapsed_s']} s, "
          f"longest block {done['longest_block_s']} s in {done['longest_block_phase']!r}")
    check("the bake finishes", done["state"] == "done", done["error"])
    check("the files it lists are there", done["files"] and all(os.path.getsize(f["path"]) == f["bytes"] for f in done["files"]), done["files"])
    check("it reports how the camera moved", done["clips"][0]["motion"] is not None, done["clips"])
    after, _ = call("import bpy\nresult = {'frame': bpy.context.scene.frame_current, 'baking': bpy.context.scene.vecbake.baking}")
    check("the frame comes back", after["frame"] == 7, after)
    check("the panel stops showing a bake", after["baking"] is False, after)

    # Selection only exists in some contexts; the connection's must be one of them.
    got, _ = call(f"""
import bpy
props = bpy.context.scene.vecbake
props.source = "SELECTED"
result = bpy.context.window_manager.vecbake.start({{"directory": {out!r}, "basename": "sel", "formats": ["json"]}})
""")
    done, _, _ = wait_done()
    check("a Selected source works from the connection", done["state"] == "done", done["error"] or got)
    call("import bpy\nbpy.context.scene.vecbake.source = 'VISIBLE'\nresult = {}")

    # The server's own way to hold one call open until a job ends.
    began = time.perf_counter()
    got, _ = call(f"""
import bpy
vb = bpy.context.window_manager.vecbake
vb.start({{"formats": []}})
def check_is_finished():
    s = vb.status()
    return None if s["state"] == "running" else s
""")
    check("one call can wait for the end", got["state"] == "done" and got["stats"]["elements"] > 0, got)
    print(f"     held the call for {time.perf_counter() - began:.1f} s")

    # Cancel mid-bake: the frame comes back, and no worker or half a job is left behind.
    vb_start = f"""
import bpy
result = bpy.context.window_manager.vecbake.start({{"directory": {out!r}, "basename": "stop", "formats": ["json"]}})
"""
    call(vb_start)
    got, _ = call("import bpy\nresult = dict(bpy.context.window_manager.vecbake.cancel())")
    check("cancel stops it", got["state"] == "cancelled", got)
    after, _ = call("import bpy\nresult = {'frame': bpy.context.scene.frame_current, 'baking': bpy.context.scene.vecbake.baking}")
    check("cancel restores the frame", after["frame"] == 7, after)
    check("cancel clears the panel", after["baking"] is False, after)

    # A second bake while one runs is refused, and does not disturb the first.
    call(vb_start)
    got, _ = call("import bpy\nresult = dict(bpy.context.window_manager.vecbake.start({'formats': []}))")
    check("a second bake is refused", got["state"] == "failed" and "already" in (got["error"] or ""), got)
    still, _ = status()
    check("and the first keeps going", still["state"] == "running", still)

    # Opening a file takes the scene away from under the bake.
    call("import bpy\nbpy.ops.wm.read_homefile(use_empty=True, use_factory_startup=True)\nresult = {}")
    time.sleep(0.5)
    got, _ = status()
    check("opening a file ends the bake", got["state"] == "cancelled" and "file" in (got["error"] or ""), got)
    time.sleep(1.0)
    got, took = status()
    check("Blender is still answering afterwards", got["state"] == "cancelled" and took < 2.0, got)


def through_bridge(exe):
    """The connector an agent actually holds: the MCP program, over stdio, pointed here."""
    env = {**os.environ, "BLENDER_MCP_HOST": "127.0.0.1", "BLENDER_MCP_PORT": str(PORT)}
    bridge = subprocess.Popen([exe], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, env=env, text=True, encoding="utf-8", bufsize=1)
    seq = [0]

    def rpc(method, params):
        seq[0] += 1
        bridge.stdin.write(json.dumps({"jsonrpc": "2.0", "id": seq[0], "method": method, "params": params}) + "\n")
        bridge.stdin.flush()
        while True:
            reply = json.loads(bridge.stdout.readline())
            if reply.get("id") == seq[0]:
                return reply["result"]

    def tool(code):
        got = rpc("tools/call", {"name": "execute_blender_code", "arguments": {"code": code}})
        body = got.get("structuredContent") or json.loads(got["content"][0]["text"])
        if body.get("status") != "ok":
            raise RuntimeError(body.get("message") or body)
        return body["result"]

    try:
        rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "agent_connection_check", "version": "1"}})
        bridge.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        bridge.stdin.flush()
        names = [t["name"] for t in rpc("tools/list", {})["tools"]]
        check("the bridge offers the tool an agent uses", "execute_blender_code" in names, names)

        tool(SCENE)
        out = os.path.join(WORK, "bridge").replace("\\", "/")
        vb = "import bpy\nvb = bpy.context.window_manager.vecbake\n"
        info = tool(vb + "result = vb.describe()")
        check("describe() through the bridge", info["addon"] == "vecbake" and info["scene"]["frame_current"] == 7, info)
        got = tool(vb + f'result = vb.start({{"directory": {out!r}, "basename": "agent", "formats": ["lerpa", "preview"]}})')
        check("start through the bridge", got["state"] == "running", got)
        while got["state"] == "running":
            time.sleep(0.5)
            got = tool(vb + "result = vb.status()")
        check("the bake ends through the bridge", got["state"] == "done", got["error"])
        check("its files are on disk", len(got["files"]) == 2 and all(os.path.getsize(f["path"]) == f["bytes"] for f in got["files"]), got["files"])
        print(f"     through the bridge: {got['elapsed_s']} s, {got['stats'].get('elements')} elements, "
              f"longest block {got['longest_block_s']} s")
        got = tool(vb + 'result = vb.start({"settings": {"edge_mode": "nope"}})')
        check("a refusal arrives through the bridge", got["state"] == "failed" and "edge_mode" in got["error"], got)
    finally:
        bridge.stdin.close()
        bridge.terminate()


def heavy(path):
    copy =os.path.join(WORK, "heavy.blend")
    shutil.copy2(path, copy)
    out = os.path.join(WORK, "heavy").replace("\\", "/")
    call(f"import bpy\nbpy.ops.wm.open_mainfile(filepath={copy.replace(chr(92), '/')!r})\nresult = {{}}", timeout=300)
    info, _ = call("import bpy\nresult = bpy.context.window_manager.vecbake.describe()")
    print("     scene", info["scene"])
    # Every stretch over a second, not just the worst, so one run finds them all.
    call("""
import sys
agent = sys.modules["bl_ext.user_default.vecbake.agent"]
agent.BLOCKS = []
if not hasattr(agent, "_plain_note_block"):
    agent._plain_note_block = agent._note_block
    def _note_block(job, phase, seconds):
        if seconds > 1.0:
            agent.BLOCKS.append((phase, round(seconds, 2)))
        agent._plain_note_block(job, phase, seconds)
    agent._note_block = _note_block
result = {}
""")
    started, took = call(f"""
import bpy
result = bpy.context.window_manager.vecbake.start({{"directory": {out!r}, "basename": "heavy", "formats": ["lerpa"]}})
""")
    print(f"     start returned in {took * 1000:.0f} ms, state {started['state']}", flush=True)
    done, worst, polls = wait_done(interval=2.0)
    print(f"     {polls} polls, slowest answer {worst:.1f} s, bake {done['elapsed_s']} s, "
          f"longest block {done['longest_block_s']} s in {done['longest_block_phase']!r}")
    blocks, _ = call("""
import sys
result = {"blocks": sys.modules["bl_ext.user_default.vecbake.agent"].BLOCKS}
""")
    print("     stretches over 1 s", blocks["blocks"])
    print("     stats", done["stats"])
    print("     files", done["files"])
    check("the heavy bake finishes", done["state"] == "done", done["error"])
    # The bridge gives up on an answer after 300 s (blmcp/tools_helpers/connection.py),
    # which a whole bake in one call does not survive: this one takes longer than that.
    check("every answer came well inside the bridge's 300 s", worst < 60.0, f"{worst:.1f} s")


def main():
    os.makedirs(WORK, exist_ok=True)
    blender = subprocess.Popen([BLENDER, "--factory-startup", "--window-geometry", "0", "0", "1280", "720",
                                "--python-expr", BOOT.replace(os.linesep, "\n")],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(240):
            try:
                call("result = {}", timeout=2)
                break
            except OSError:
                time.sleep(0.5)
        else:
            raise SystemExit("the server never came up")
        call("""
import addon_utils
addon_utils.enable("bl_ext.user_default.vecbake", default_set=False, persistent=False)
result = {}
""")
        controlled()
        if os.environ.get("BLENDER_MCP_EXE"):
            through_bridge(os.environ["BLENDER_MCP_EXE"])
        if HEAVY:
            heavy(HEAVY)
    finally:
        # By PID: this must never touch a Blender someone else has open.
        blender.terminate()
    print("FAILED " + str(len(FAILS)) if FAILS else "all passed")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
