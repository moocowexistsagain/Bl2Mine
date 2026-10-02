#!/usr/bin/env python3
"""Cross-process self-test for the BorderCraft shared-memory protocol.

Spawns two real OS processes (a fake BL2 host and a fake Minecraft client) that talk
only through the mapping file, exactly like the production mod pair will:

  - handshake (magic/version/pids/heartbeats)
  - BL2 -> MC: seqlocked Bl2State, actor table, collision ring, input ring
  - MC -> BL2: seqlocked McState, event ring, teleport ack

Usage:  python3 tools/protocol_selftest.py            (runs both sides)
        python3 tools/protocol_selftest.py --bl2 PATH  (internal: one side)
        python3 tools/protocol_selftest.py --mc  PATH
"""
from __future__ import annotations

import os
import struct
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "protocol", "python"))
import bordercraft_protocol as P  # noqa: E402

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

DEADLINE_S = 20.0
LAST_ERR: list[str] = []

PATTERN_W = PATTERN_H = 64


def make_pattern(fid: int) -> bytes:
    """Deterministic per-frame BGRA test pattern (shared by publisher and validator)."""
    out = bytearray()
    for y in range(PATTERN_H):
        for x in range(PATTERN_W):
            out += bytes(((x + fid) & 0xFF, (y + fid) & 0xFF, fid & 0xFF, 255))
    return bytes(out)


def wait_until(fn, what: str, timeout: float = DEADLINE_S, diag=None):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.005)
    extra = diag() if diag else ""
    raise AssertionError(f"timeout waiting for {what}{extra}")


def module_banner(side: str) -> str:
    return (f"[{side}] module {os.path.abspath(P.__file__)} "
            f"MAPPING_BYTES={P.MAPPING_BYTES}")


def bl2_side(path: str) -> None:
    print(module_banner("bl2 "))
    try:
        br = P.Bridge.create(path)
    except RuntimeError as e:
        print(f"[bl2 ] FATAL: {e}")
        raise
    br.write_header(bl2_pid=os.getpid())
    print(f"[bl2 ] bridge created at {path} (file size {os.path.getsize(path)} bytes)")
    ok = 0

    # 1. handshake: wait until MC side registers itself
    def hs_diag() -> str:
        size = os.path.getsize(path) if os.path.exists(path) else "<missing>"
        return (f"\n  bridge file size={size}, expected {P.MAPPING_BYTES}"
                f"\n  (check the 'mc' side's output above - it explains why it cannot open "
                f"the bridge; a stale or mixed copy of bordercraft_protocol.py is the usual "
                f"cause if the sizes differ)")

    wait_until(lambda: br.read_header().mc_pid != 0, "MC handshake", diag=hs_diag)
    print(f"[bl2 ] handshake ok (mc pid {br.read_header().mc_pid})")
    ok += 1

    # 2. publish world state
    st = P.Bl2State(
        flags=P.BL2_IN_GAME, world_id=0xC0FFEE, collision_epoch=1,
        pos_x=10.0, pos_y=64.0, pos_z=-5.0, yaw=90.0, pitch=-10.0,
        teleport_seq=0, viewport_w=1920, viewport_h=1080, game_speed=1.0,
    )
    br.write_bl2_state(st)

    # 3. actors: two bandits
    br.write_actors([
        P.ActorEntry(id=7, flags=P.ACTOR_HOSTILE | P.ACTOR_IN_COMBAT, x=12.0, y=64.0, z=-5.0,
                     yaw=180.0, health=80.0, max_health=100.0, half_w=0.4, height=1.9),
        P.ActorEntry(id=9, flags=P.ACTOR_HOSTILE, x=14.0, y=64.0, z=-3.0,
                     yaw=135.0, health=100.0, max_health=100.0, half_w=0.4, height=1.9),
    ])

    # 4. collision: three heightfield sections at 1/8-block resolution
    for i, (sx, sy, sz) in enumerate([(0, 4, 0), (1, 4, 0), (0, 4, 1)]):
        payload = b"".join(struct.pack("<hBB", 64 * 8 + (x + z) % 3, P.COL_WALKABLE, 0)
                           for z in range(16) for x in range(16))
        rec = br.pack_collision(epoch=1, sec_x=sx, sec_y=sy, sec_z=sz,
                                kind=P.COL_HEIGHTFIELD, payload=payload)
        wait_until(lambda rec=rec: br.push_collision(rec), f"collision push {i}")

    # 5. input: 40 mouse moves
    for i in range(40):
        wait_until(lambda i=i: br.push_input(P.InputEntry(P.IN_MOUSE_MOVE, 0, i, -i, P.monotonic_ms())),
                   f"input push {i}")

    # 6. consume MC events: expect a hit and a block place
    events = wait_until(lambda: [e for e in _drain(br.pop_event)] or None, "MC events")
    assert len(events) == 2, f"expected 2 events, got {len(events)}"
    hit = next(e for e in events if e.type == P.EVT_PLAYER_HIT_ACTOR)
    place = next(e for e in events if e.type == P.EVT_BLOCK_PLACE)
    assert hit.actor_id == 7 and abs(hit.a - 21.5) < 1e-3, f"bad hit event: {hit}"
    assert (place.x, place.y, place.z) == (3.0, 5.0, -2.0), f"bad place event: {place}"
    print(f"[bl2 ] events ok (hit {hit.a} dmg on actor {hit.actor_id}, block place at "
          f"{place.x:.0f},{place.y:.0f},{place.z:.0f})")
    ok += 1

    # 7. read the walking player
    mc = wait_until(lambda: (lambda s: s if s and s.x > 9.5 else None)(br.read_mc_state()),
                    "MC walk state")
    print(f"[bl2 ] mc state ok (walked to x={mc.x:.2f}, on_ground={bool(mc.flags & P.MC_ON_GROUND)})")
    ok += 1

    # 8. teleport request -> wait for ack
    st.teleport_seq = 1
    st.pos_x, st.pos_y, st.pos_z = -100.0, 70.0, 250.0
    br.write_bl2_state(st)
    mc = wait_until(lambda: (lambda s: s if s and s.teleport_ack == 1 else None)(br.read_mc_state()),
                    "teleport ack")
    assert abs(mc.x + 100.0) < 1e-6, f"teleport not applied: {mc.x}"
    print("[bl2 ] teleport handshake ok")
    ok += 1

    # 9. the v2 gameplay regions: everything the overlay needs to draw Minecraft in Pandora
    pose = wait_until(lambda: (lambda p: p if p and p.limb_swing_amount > 0 else None)(br.read_pose()),
                      "pose")
    assert pose.flags & P.POSE_SLIM and abs(pose.body_yaw - 90.0) < 1e-3, f"bad pose: {pose}"
    assert abs(pose.head_yaw - 100.0) < 1e-3 and abs(pose.limb_swing - 4.25) < 1e-3
    hud = wait_until(lambda: (lambda h: h if h and h.slots else None)(br.read_hud()), "HUD")
    assert len(hud.slots) == 41, f"expected 41 inventory slots, got {len(hud.slots)}"
    assert hud.selected_slot == 4 and hud.slots[4].flags & P.SLOT_SELECTED
    assert hud.slots[7].name == "Slot 7", f"slot name round trip failed: {hud.slots[7].name!r}"
    assert abs(hud.health - 15.5) < 1e-3 and hud.xp_level == 24
    blocks = wait_until(lambda: (lambda b: b if b and b[0] else None)(br.read_blocks()), "blocks")
    entries, world_id, revision = blocks
    assert len(entries) == 49 and world_id == 0xBEEF and revision == 11
    assert any(e.flags & P.BLOCK_FULL_CUBE for e in entries)
    print(f"[bl2 ] gameplay regions ok (pose yaw {pose.body_yaw:.0f}, {len(hud.slots)} slots, "
          f"{len(entries)} blocks)")
    ok += 1

    # 10. damage flowing the other way: a bandit shoots the Minecraft player.
    wait_until(lambda: br.push_bl2_event(P.McEvent(
        type=P.EVT_BL2_DAMAGE_PLAYER, flags=P.DAMAGE_GUN, a=3.5)), "bl2 damage push")
    wait_until(lambda: br.push_bl2_event(P.McEvent(
        type=P.EVT_BL2_ACTOR_DIED, actor_id=9)), "bl2 death push")

    # 11. overlay frames: acquire and validate what "Minecraft" rendered
    seen: dict[int, bool] = {}

    def got_frame():
        fr = br.overlay.acquire()
        if fr is None:
            return None
        fid, w, h, flags, pixels = fr
        assert (w, h) == (PATTERN_W, PATTERN_H), f"bad overlay size {w}x{h}"
        if pixels != make_pattern(fid):
            return None  # torn copy raced a rewrite; production just draws the next frame
        if seen:
            assert fid >= max(seen), f"frames went backwards: {max(seen)} -> {fid}"
        seen[fid] = True
        return 30 in seen

    wait_until(got_frame, "overlay frames")
    print(f"[bl2 ] overlay ok ({len(seen)} frames acquired, latest {max(seen)})")
    ok += 1

    br.heartbeat("bl2")
    wait_until(lambda: br.peer_alive("mc"), "MC heartbeat")
    print(f"[bl2 ] PASS ({ok} stages)")
    br.close()


def mc_side(path: str) -> None:
    print(module_banner("mc  "))

    def open_diag() -> str:
        size = os.path.getsize(path) if os.path.exists(path) else "<missing>"
        err = LAST_ERR[-1] if LAST_ERR else "none"
        return (f"\n  bridge file size={size}, expected {P.MAPPING_BYTES}"
                f"\n  last open error: {err}"
                f"\n  module: {os.path.abspath(P.__file__)}"
                f"\n  (if the sizes differ between the two sides you have a stale or mixed "
                f"copy of bordercraft_protocol.py - delete __pycache__ folders and re-copy)")

    br = wait_until(lambda: _try_open(path), "bridge file", diag=open_diag)
    br.write_header(mc_pid=os.getpid())
    print(f"[mc  ] handshake ok (bl2 pid {br.read_header().bl2_pid})")

    st = wait_until(lambda: (lambda s: s if s and s.world_id else None)(br.read_bl2_state()),
                    "Bl2State")
    assert st.world_id == 0xC0FFEE and st.flags & P.BL2_IN_GAME
    actors = wait_until(br.read_actors, "actors")
    assert len(actors) == 2 and actors[0].id == 7
    print(f"[mc  ] world ok (worldId={st.world_id:#x}, {len(actors)} actors)")

    cols = []
    while len(cols) < 3:
        cols.extend(_drain(br.pop_collision))
    for rec in cols:
        seq, epoch, sec, kind, count, flags, payload = br.unpack_collision(rec)
        assert epoch == 1 and kind == P.COL_HEIGHTFIELD
        h0 = struct.unpack_from("<h", payload, 0)[0]
        assert h0 == 64 * 8, f"bad heightfield {h0}"
    print(f"[mc  ] collision ok ({len(cols)} sections)")

    inputs = []
    while len(inputs) < 40:
        inputs.extend(_drain(br.pop_input))
    assert inputs[-1].value == 39 and inputs[0].type == P.IN_MOUSE_MOVE
    print(f"[mc  ] input ring ok ({len(inputs)} events)")

    # walk: 100 frames, 0.1 blocks/frame along +x, publish state, then two events
    for i in range(100):
        br.write_mc_state(P.McState(
            flags=P.MC_IN_WORLD | P.MC_ON_GROUND, x=i * 0.1, y=64.0, z=-5.0,
            yaw=90.0, pitch=0.0, frame_counter=i, tick_ms=50.0,
        ))
        br.heartbeat("mc")
        time.sleep(0.001)

    br.push_event(P.McEvent(P.EVT_PLAYER_HIT_ACTOR, 0, actor_id=7, a=21.5, b=0.0, c=1.0,
                            x=12.0, y=65.0, z=-5.0))
    br.push_event(P.McEvent(P.EVT_BLOCK_PLACE, 3, actor_id=0, x=3.0, y=5.0, z=-2.0))

    # teleport request: apply and ack
    def apply_teleport():
        s = br.read_bl2_state()
        if s and s.teleport_seq == 1:
            br.write_mc_state(P.McState(
                flags=P.MC_IN_WORLD | P.MC_ON_GROUND, x=s.pos_x, y=s.pos_y, z=s.pos_z,
                teleport_ack=1, frame_counter=100, tick_ms=50.0,
            ))
            return True
        return False

    wait_until(apply_teleport, "teleport request")
    print("[mc  ] teleport applied")

    # v2 gameplay regions: the pose that drives the 3D model, the whole HUD/inventory, and
    # the blocks the player has placed.
    br.write_pose(P.PoseState(
        flags=P.POSE_SLIM | P.POSE_ON_GROUND, body_yaw=90.0, head_yaw=100.0, head_pitch=-12.0,
        limb_swing=4.25, limb_swing_amount=0.9, hand_swing=0.25, sneak_amount=0.0,
        scale=1.0, vel_x=0.21, vel_y=-0.08, vel_z=0.0, held_main_rgb=0x7FBF3F,
    ))
    br.write_hud(P.HudState(
        flags=0, health=15.5, max_health=20.0, armor=8.0, food=17.0, saturation=3.5,
        air=300.0, max_air=300.0, xp_level=24, xp_progress=0.75, selected_slot=4,
        slots=[P.HudSlot(item_hash=0x1000 + i, count=(i % 64) + 1, rgb=0x8B6A4F + i * 7,
                         flags=P.SLOT_BLOCK | (P.SLOT_SELECTED if i == 4 else 0),
                         damage=0, name=f"Slot {i}")
               for i in range(41)],
    ))
    br.write_blocks([P.BlockEntry(x, 64, z, 180, 170, 160, P.BLOCK_FULL_CUBE)
                     for x in range(-3, 4) for z in range(-3, 4)],
                    world_id=0xBEEF, revision=11)
    print("[mc  ] published pose, HUD (41 slots) and 49 blocks")

    # BL2 -> MC damage: the bandit's bullet, expressed in Minecraft half-hearts.
    hits = wait_until(lambda: [e for e in _drain(br.pop_bl2_event)] or None, "BL2 damage events")
    assert len(hits) == 2, f"expected 2 BL2 events, got {len(hits)}"
    shot = next(e for e in hits if e.type == P.EVT_BL2_DAMAGE_PLAYER)
    died = next(e for e in hits if e.type == P.EVT_BL2_ACTOR_DIED)
    assert shot.flags == P.DAMAGE_GUN and abs(shot.a - 3.5) < 1e-3, f"bad damage event: {shot}"
    assert died.actor_id == 9, f"bad death event: {died}"
    print(f"[mc  ] bl2 events ok ({shot.a} hearts of {shot.flags} damage, actor "
          f"{died.actor_id} died)")

    # overlay: publish 30 rendered frames (MC renders -> BL2 composites)
    for fid in range(1, 31):
        br.overlay.publish(PATTERN_W, PATTERN_H, make_pattern(fid))
        time.sleep(0.002)
    print("[mc  ] overlay: published 30 frames")

    wait_until(lambda: br.peer_alive("bl2"), "BL2 heartbeat")
    print("[mc  ] PASS")
    br.close()


def _drain(pop):
    out = []
    while True:
        rec = pop()
        if rec is None:
            return out
        out.append(rec)


def _try_open(path: str):
    try:
        return P.Bridge.open(path)
    except Exception as e:  # missing, partial, stale, wrong build: all retryable
        LAST_ERR.append(repr(e))
        return None


def main() -> int:
    args = sys.argv[1:]
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "selftest.mm")

    if args and args[0] in ("--bl2", "--mc"):
        (bl2_side if args[0] == "--bl2" else mc_side)(args[1])
        return 0

    # preflight: a partial bridge file must be a retryable error, never a crash
    with open(path, "wb") as f:
        f.truncate(1024 * 1024)
    try:
        P.Bridge.open(path)
    except RuntimeError as e:
        assert "1048576" in str(e), f"unexpected error: {e}"
        print(f"[pre ] partial-file guard ok ({str(e).split('(')[0].strip()})")
    else:
        print("FAIL: short bridge file opened")
        return 1

    # clear any stale file; on Windows a mapped file cannot be deleted - retry briefly
    for attempt in range(20):
        try:
            if os.path.exists(path):
                os.remove(path)
            break
        except PermissionError:
            time.sleep(0.25)
    else:
        print(f"FAIL: cannot delete {path} - a previous run's process is still holding it.\n"
              "Close stray python.exe / javaw.exe / Borderlands2.exe and try again.")
        return 1

    child = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--bl2", path])
    try:
        mc_side(path)
    finally:
        rc = child.wait(timeout=DEADLINE_S + 5)
    if rc != 0:
        print("FAIL: bl2 side exited", rc)
        return 1
    print("protocol selftest: ALL PASS")
    try:
        os.remove(path)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
