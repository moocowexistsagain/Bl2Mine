"""BorderCraft shared-memory protocol — Python mirror.

Mirror of protocol/bordercraft_protocol.h (the single source of truth). Keep in sync with
that header and with fabric/src/main/java/dev/bordercraft/link/Proto.java, and bump VERSION
on any layout change.

Stdlib-only so it runs both inside Borderlands 2's Python SDK and in tools/ scripts.
"""
from __future__ import annotations

import mmap
import os
import struct
import time
from dataclasses import dataclass
from typing import Iterator, Optional

MAGIC = 0x54464342  # "BCFT"
VERSION = 1
MAPPING_FILENAME = "bridge.mm"
UNITS_PER_BLOCK = 53.3333  # Unreal units per Minecraft block

# ---- region offsets ----------------------------------------------------------------------
OFF_HEADER = 0x0
OFF_BL2_STATE = 0x100
OFF_MC_STATE = 0x200
OFF_OVERLAY_CTL = 0x300
OFF_OVERLAY_SLOT_HDR = 0x340
OFF_WATER_GRID = 0x400
OFF_INPUT_RING = 0x1000
OFF_COLLISION_RING = 0x12000
OFF_ACTOR_TABLE = 0x453000
OFF_EVENT_RING = 0x457000
OFF_OVERLAY_PIXELS = 0x480000

MAX_OVERLAY_W = 1920
MAX_OVERLAY_H = 1080
OVERLAY_SLOT_BYTES = MAX_OVERLAY_W * MAX_OVERLAY_H * 4
OVERLAY_SLOTS = 2
MAPPING_BYTES = OFF_OVERLAY_PIXELS + OVERLAY_SLOT_BYTES * OVERLAY_SLOTS  # 0x1452000

INPUT_RING_ENTRIES = 4096
INPUT_RING_BASE = 0x80
COLLISION_RING_ENTRIES = 4096
COLLISION_RING_BASE = 0x80
COLLISION_REC_BYTES = 0x440
MAX_ACTORS = 256
EVENT_RING_ENTRIES = 4096
EVENT_RING_BASE = 0x80

# ---- flags ---------------------------------------------------------------------------------
BL2_IN_GAME = 1 << 0
BL2_MENU_OPEN = 1 << 1
BL2_LOADING = 1 << 2
BL2_PAUSED = 1 << 3

MC_IN_WORLD = 1 << 0
MC_SCREEN_OPEN = 1 << 1
MC_ON_GROUND = 1 << 2
MC_SNEAKING = 1 << 3
MC_SPRINTING = 1 << 4
MC_DEAD = 1 << 5
MC_SWIMMING = 1 << 6
MC_FLYING = 1 << 7

OVERLAY_DIRTY = 1 << 2

COL_WALKABLE = 1 << 0
COL_WATER = 1 << 1
COL_STEEP = 1 << 2
COL_HEIGHTFIELD = 0
COL_AABBS = 1

ACTOR_HOSTILE = 1 << 0
ACTOR_DEAD = 1 << 1
ACTOR_BOSS = 1 << 2
ACTOR_TARGETED = 1 << 3
ACTOR_IN_COMBAT = 1 << 4

# input types
IN_KEY_DOWN, IN_KEY_UP, IN_MOUSE_MOVE = 1, 2, 3
IN_MOUSE_DOWN, IN_MOUSE_UP, IN_MOUSE_WHEEL, IN_FOCUS_LOST = 4, 5, 6, 7

# event types
EVT_PLAYER_HIT_ACTOR, EVT_ACTOR_HIT_PLAYER = 1, 2
EVT_BLOCK_PLACE, EVT_BLOCK_BREAK = 3, 4
EVT_PLAYER_DIED, EVT_PLAYER_RESPAWNED = 5, 6
EVT_SOUND_PLAY, EVT_APPROACH_ACTOR = 7, 8

NO_WATER = -1.0e30

_HDR_FMT = "<IIIIQQ"
_BL2_FMT = "<IIIIdddffIIIf"
_MC_FMT = (
    "<II" "ddd" "ffff" "II" "Q" "fff" "I"
    "ddd" "q" "dddddd" "fffffff" "IIf"
)
_INPUT_FMT = "<HHiiI"
_EVENT_FMT = "<HHIffffff"
_ACTOR_FMT = "<IIffffffffQ"

assert struct.calcsize(_BL2_FMT) == 0x40
assert struct.calcsize(_MC_FMT) == 0xC8
assert struct.calcsize(_INPUT_FMT) == 0x10
assert struct.calcsize(_EVENT_FMT) == 0x20
assert struct.calcsize(_ACTOR_FMT) == 0x30


def default_mapping_path() -> str:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "BorderCraft", MAPPING_FILENAME)
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return os.path.join(base, f"bordercraft-{os.getuid()}", MAPPING_FILENAME)


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


# ---- dataclasses (for API convenience; bytes on the wire are the structs above) ------------
@dataclass
class Header:
    magic: int = MAGIC
    version: int = VERSION
    bl2_pid: int = 0
    mc_pid: int = 0
    bl2_heartbeat_ms: int = 0
    mc_heartbeat_ms: int = 0


@dataclass
class Bl2State:
    flags: int = 0
    world_id: int = 0
    collision_epoch: int = 0
    pos_x: float = 0.0
    pos_y: float = 0.0
    pos_z: float = 0.0
    yaw: float = 0.0
    pitch: float = 0.0
    teleport_seq: int = 0
    viewport_w: int = 0
    viewport_h: int = 0
    game_speed: float = 1.0


@dataclass
class McState:
    flags: int = 0
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    yaw: float = 0.0
    pitch: float = 0.0
    eye_height: float = 1.62
    sensitivity: float = 0.5
    teleport_ack: int = 0
    gui_scale: int = 2
    frame_counter: int = 0
    fov_deg: float = 70.0
    bob_phase: float = 0.0
    bob_amount: float = 0.0
    eye_x: float = 0.0
    eye_y: float = 0.0
    eye_z: float = 0.0
    tick_qpc: int = 0
    prev_x: float = 0.0
    prev_y: float = 0.0
    prev_z: float = 0.0
    cur_x: float = 0.0
    cur_y: float = 0.0
    cur_z: float = 0.0
    tick_eye_o: float = 1.62
    tick_eye: float = 1.62
    walk_dist_o: float = 0.0
    walk_dist: float = 0.0
    bob_o: float = 0.0
    bob: float = 0.0
    tick_ms: float = 50.0
    camera_mode: int = 0
    camera_distance: float = 4.0


@dataclass
class InputEntry:
    type: int = 0
    code: int = 0
    value: int = 0
    aux: int = 0
    time_ms: int = 0


@dataclass
class McEvent:
    type: int = 0
    flags: int = 0
    actor_id: int = 0
    a: float = 0.0
    b: float = 0.0
    c: float = 0.0
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@dataclass
class ActorEntry:
    id: int = 0
    flags: int = 0
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    yaw: float = 0.0
    health: float = 0.0
    max_health: float = 0.0
    half_w: float = 0.5
    height: float = 1.8
    extra: int = 0


class Seqlock:
    """Seqlock over a region: seq (u32) is odd while the writer is writing.

    Cross-process-safe for one writer + one reader when the writer follows write():
    bump odd -> payload -> bump even. Readers retry while seq changed or is odd.
    """

    def __init__(self, buf: mmap.mmap, offset: int):
        self.buf = buf
        self.off = offset

    def begin_write(self) -> int:
        """Bump to odd and return the odd marker to embed in the payload's seq field."""
        seq = self._get() + 1
        self._set(seq)
        return seq

    def end_write(self, odd_seq: int) -> None:
        self._set(odd_seq + 1)

    def try_read(self) -> Optional[int]:
        """Returns the stable seq if a consistent read is available, else None."""
        a = self._get()
        if a & 1:
            return None
        return a

    def read_ok(self, seq_before: int) -> bool:
        return self._get() == seq_before and (seq_before & 1) == 0

    def _get(self) -> int:
        return struct.unpack_from("<I", self.buf, self.off)[0]

    def _set(self, v: int) -> None:
        struct.pack_into("<I", self.buf, self.off, v & 0xFFFFFFFF)


class Ring:
    """Single-producer single-consumer ring buffer of fixed-size records.

    head is written by the producer, tail by the consumer; both are monotonic u64 counters
    (index into the ring, masked for the slot). Same layout family as SkyCraft's rings.
    """

    def __init__(self, buf: mmap.mmap, base: int, entries: int, rec_bytes: int):
        self.buf = buf
        self.head_off = base + 0x00
        self.tail_off = base + 0x40
        self.data_off = base + 0x80
        self.entries = entries
        self.rec_bytes = rec_bytes

    def _u64(self, off: int) -> int:
        return struct.unpack_from("<Q", self.buf, off)[0]

    def _set_u64(self, off: int, v: int) -> None:
        struct.pack_into("<Q", self.buf, off, v)

    def push(self, record: bytes) -> bool:
        assert len(record) == self.rec_bytes
        head = self._u64(self.head_off)
        tail = self._u64(self.tail_off)
        if head - tail >= self.entries:
            return False  # full: producer drops (caller may retry)
        off = self.data_off + (head % self.entries) * self.rec_bytes
        self.buf[off:off + self.rec_bytes] = record
        self._set_u64(self.head_off, head + 1)
        return True

    def pop(self) -> Optional[bytes]:
        tail = self._u64(self.tail_off)
        head = self._u64(self.head_off)
        if tail == head:
            return None
        off = self.data_off + (tail % self.entries) * self.rec_bytes
        rec = bytes(self.buf[off:off + self.rec_bytes])
        self._set_u64(self.tail_off, tail + 1)
        return rec

    def pop_all(self) -> Iterator[bytes]:
        while True:
            rec = self.pop()
            if rec is None:
                return
            yield rec


class Overlay:
    """Frame double buffer (SkyCraft's overlay scheme, 2 slots).

    Writer (Minecraft) renders into its private back slot, publishes by swapping state to
    `slot | DIRTY`, and keeps the previously-published slot as its new back. Reader (BL2)
    takes the newest frame when dirty and clears the flag. A frameId written into the slot
    header after the pixels lets the reader detect a copy that raced a rewrite.
    """

    def __init__(self, buf: mmap.mmap):
        self.buf = buf
        self._back = 0          # writer-private slot
        self._frames = 0

    @staticmethod
    def _slot_hdr(i: int) -> int:
        return OFF_OVERLAY_SLOT_HDR + i * 0x40

    @staticmethod
    def _slot_px(i: int) -> int:
        return OFF_OVERLAY_PIXELS + i * OVERLAY_SLOT_BYTES

    def _state(self) -> int:
        return struct.unpack_from("<I", self.buf, OFF_OVERLAY_CTL)[0]

    def _set_state(self, v: int) -> None:
        struct.pack_into("<I", self.buf, OFF_OVERLAY_CTL, v & 0xFFFFFFFF)

    def publish(self, w: int, h: int, pixels: bytes, bottom_up: bool = True) -> int:
        """Writer: copy one BGRA frame into the back slot and publish it. Returns frame id."""
        if not (0 < w <= MAX_OVERLAY_W and 0 < h <= MAX_OVERLAY_H):
            raise ValueError(f"bad overlay size {w}x{h}")
        need = w * h * 4
        if len(pixels) != need:
            raise ValueError(f"overlay pixels: got {len(pixels)}, want {need}")
        slot = self._back
        self._frames += 1
        hdr = self._slot_hdr(slot)
        struct.pack_into("<IIII", self.buf, hdr, w, h, 1 if bottom_up else 0, 0)
        px = self._slot_px(slot)
        self.buf[px:px + need] = pixels
        struct.pack_into("<Q", self.buf, hdr + 0x10, self._frames)  # ready marker, written last
        old = self._state()
        self._set_state(slot | OVERLAY_DIRTY)
        new_back = old & 0x3
        self._back = new_back if new_back != slot and new_back < OVERLAY_SLOTS else (slot + 1) % OVERLAY_SLOTS
        return self._frames

    def acquire(self):
        """Reader: newest unpublished frame, or None. Returns (frame_id, w, h, flags, pixels)."""
        state = self._state()
        if not (state & OVERLAY_DIRTY):
            return None
        slot = state & 0x3
        if slot >= OVERLAY_SLOTS:
            return None
        hdr = self._slot_hdr(slot)
        fid = struct.unpack_from("<Q", self.buf, hdr + 0x10)[0]
        w, h, flags, _ = struct.unpack_from("<IIII", self.buf, hdr)
        if fid == 0 or w == 0 or h == 0:
            self._set_state(state & ~OVERLAY_DIRTY)
            return None
        need = w * h * 4
        px = self._slot_px(slot)
        pixels = bytes(self.buf[px:px + need])
        if struct.unpack_from("<Q", self.buf, hdr + 0x10)[0] != fid:
            return None  # writer rewrote the slot mid-copy; drop and retry next call
        if self._state() == state:  # don't clobber a newer publish
            self._set_state(slot)
        return (fid, w, h, flags, pixels)


class Bridge:
    """The shared mapping, with typed accessors. BL2 creates it; Minecraft opens it."""

    def __init__(self, buf: mmap.mmap, path: str = ""):
        if len(buf) < MAPPING_BYTES:
            raise RuntimeError(f"mapping too small: {len(buf)} < {MAPPING_BYTES}")
        self.buf = buf
        self.path = path
        self.header = Seqlock(buf, OFF_HEADER)  # header writes are whole-struct, no seq
        self.bl2 = Seqlock(buf, OFF_BL2_STATE)
        self.mc = Seqlock(buf, OFF_MC_STATE)
        self.water = Seqlock(buf, OFF_WATER_GRID)
        self.actors = Seqlock(buf, OFF_ACTOR_TABLE)
        self.input_ring = Ring(buf, OFF_INPUT_RING, INPUT_RING_ENTRIES, struct.calcsize(_INPUT_FMT))
        self.collision_ring = Ring(buf, OFF_COLLISION_RING, COLLISION_RING_ENTRIES, COLLISION_REC_BYTES)
        self.event_ring = Ring(buf, OFF_EVENT_RING, EVENT_RING_ENTRIES, struct.calcsize(_EVENT_FMT))
        self.overlay = Overlay(buf)

    # -- lifecycle ---------------------------------------------------------------------------
    @classmethod
    def create(cls, path: Optional[str] = None, retries: int = 50) -> "Bridge":
        """Create (or take over) the mapping and write the header. BL2 side.

        Never open an existing bridge with a truncating mode: Windows can report EINVAL
        (ERROR_USER_MAPPED_FILE) when Minecraft still maps it. Reuse a correctly sized file
        in place, and resize only when necessary. Reset all shared data before publishing
        the magic so old ring counters, states and overlay frames do not survive a restart.
        """
        if retries < 1:
            raise ValueError("retries must be at least 1")
        path = os.fspath(path or default_mapping_path())
        last: Exception | None = None
        operation = "creating bridge directory"
        for attempt in range(retries):
            buf = None
            try:
                operation = "creating bridge directory"
                directory = os.path.dirname(path)
                if directory:
                    os.makedirs(directory, exist_ok=True)

                operation = "opening bridge file without truncation"
                try:
                    f = open(path, "r+b")
                except FileNotFoundError:
                    # Exclusive creation avoids truncating a file that appeared after r+b
                    # failed. If another process won that race, retry and reopen it instead.
                    f = open(path, "x+b")
                with f:
                    operation = "checking bridge file size"
                    size = os.fstat(f.fileno()).st_size
                    if size != MAPPING_BYTES:
                        operation = f"resizing bridge file from {size} to {MAPPING_BYTES} bytes"
                        f.truncate(MAPPING_BYTES)
                        f.flush()

                    operation = "checking bridge file size"
                    size = os.fstat(f.fileno()).st_size
                    if size != MAPPING_BYTES:
                        raise RuntimeError(f"bridge file came up {size} bytes, expected {MAPPING_BYTES}")
                    operation = f"mapping {MAPPING_BYTES} bytes"
                    buf = mmap.mmap(f.fileno(), MAPPING_BYTES, access=mmap.ACCESS_WRITE)

                operation = "resetting bridge memory"
                struct.pack_into("<I", buf, OFF_HEADER, 0)  # invalidate any previous header first
                # BL2 is a 32-bit process; avoid allocating another full ~21 MB buffer to clear it.
                zeros = b"\x00" * 65536
                for offset in range(0, MAPPING_BYTES, len(zeros)):
                    end = min(offset + len(zeros), MAPPING_BYTES)
                    buf[offset:end] = zeros[:end - offset]
                br = cls(buf, path)
                operation = "initializing bridge header"
                struct.pack_into(_HDR_FMT, buf, OFF_HEADER, 0, VERSION, os.getpid(), 0, monotonic_ms(), 0)
                struct.pack_into("<I", buf, OFF_HEADER, MAGIC)  # publish only after initialization
                buf = None  # ownership passes to br; failed attempts are closed below
                return br
            except (OSError, ValueError, RuntimeError) as e:
                last = e
            finally:
                if buf is not None:
                    buf.close()
            if attempt + 1 < retries:
                time.sleep(0.1)

        if operation.startswith("resizing"):
            hint = (
                "Check that the bridge directory is writable. A different-sized bridge cannot "
                "be resized while it is mapped on Windows. Close both games and any leftover "
                "BorderCraft processes before deleting the bridge file, then restart."
            )
        else:
            hint = (
                "Check that the path is valid and the bridge directory is writable. If the file "
                "is locked, close both games and any leftover BorderCraft processes, then retry."
            )
        raise RuntimeError(f"could not create bridge at {path} while {operation}: {last}\n{hint}") from last

    @classmethod
    def open(cls, path: Optional[str] = None) -> "Bridge":
        """Open an existing mapping. Minecraft side (or a tool).

        Raises RuntimeError (retryable) while the file is missing, partial, or not yet
        initialized - never asserts. A consumer should simply try again.
        """
        path = path or default_mapping_path()
        try:
            size = os.path.getsize(path)
        except OSError as e:
            raise RuntimeError(f"bridge not ready at {path}: {e}") from e
        if size < MAPPING_BYTES:
            raise RuntimeError(
                f"bridge file at {path} is {size} bytes; expected {MAPPING_BYTES} "
                "(incomplete, or written by a different build of bordercraft_protocol)"
            )
        f = open(path, "r+b")
        buf = mmap.mmap(f.fileno(), 0)
        f.close()
        if len(buf) < MAPPING_BYTES:
            buf.close()
            raise RuntimeError(f"bridge map at {path} is {len(buf)} bytes; expected {MAPPING_BYTES}")
        br = cls(buf, path)
        magic, version = struct.unpack_from("<II", buf, OFF_HEADER)
        if magic != MAGIC:
            br.close()
            raise RuntimeError(f"bad magic {magic:#x} at {path} (not yet initialized)")
        if version != VERSION:
            br.close()
            raise RuntimeError(f"protocol version {version} != {VERSION}")
        return br

    def close(self) -> None:
        self.buf.close()

    # -- header ------------------------------------------------------------------------------
    def write_header(self, bl2_pid: Optional[int] = None, mc_pid: Optional[int] = None) -> None:
        magic, version, bpid, mpid, bhb, mhb = struct.unpack_from(_HDR_FMT, self.buf, OFF_HEADER)
        now = monotonic_ms()
        if bl2_pid is not None:
            bpid, bhb = bl2_pid, now
        if mc_pid is not None:
            mpid, mhb = mc_pid, now
        struct.pack_into(_HDR_FMT, self.buf, OFF_HEADER, magic, version, bpid, mpid, bhb, mhb)

    def heartbeat(self, side: str) -> None:
        now = monotonic_ms()
        off = OFF_HEADER + (16 if side == "bl2" else 24)
        struct.pack_into("<Q", self.buf, off, now)

    def read_header(self) -> Header:
        magic, version, bpid, mpid, bhb, mhb = struct.unpack_from(_HDR_FMT, self.buf, OFF_HEADER)
        return Header(magic, version, bpid, mpid, bhb, mhb)

    def peer_alive(self, side: str, timeout_ms: int = 2000) -> bool:
        h = self.read_header()
        beat = h.bl2_heartbeat_ms if side == "bl2" else h.mc_heartbeat_ms
        return beat != 0 and (monotonic_ms() - beat) < timeout_ms

    # -- Bl2State / McState ------------------------------------------------------------------
    def write_bl2_state(self, s: Bl2State) -> None:
        seq = self.bl2.begin_write()
        struct.pack_into(
            _BL2_FMT, self.buf, OFF_BL2_STATE, seq & 0xFFFFFFFF,
            s.flags, s.world_id, s.collision_epoch,
            s.pos_x, s.pos_y, s.pos_z, s.yaw, s.pitch,
            s.teleport_seq, s.viewport_w, s.viewport_h, s.game_speed,
        )
        self.bl2.end_write(seq)

    def read_bl2_state(self) -> Optional[Bl2State]:
        seq = self.bl2.try_read()
        if seq is None:
            return None
        vals = struct.unpack_from(_BL2_FMT, self.buf, OFF_BL2_STATE)
        if not self.bl2.read_ok(seq):
            return None
        return Bl2State(
            flags=vals[1], world_id=vals[2], collision_epoch=vals[3],
            pos_x=vals[4], pos_y=vals[5], pos_z=vals[6], yaw=vals[7], pitch=vals[8],
            teleport_seq=vals[9], viewport_w=vals[10], viewport_h=vals[11], game_speed=vals[12],
        )

    def write_mc_state(self, s: McState) -> None:
        seq = self.mc.begin_write()
        struct.pack_into(
            _MC_FMT, self.buf, OFF_MC_STATE, seq & 0xFFFFFFFF,
            s.flags, s.x, s.y, s.z, s.yaw, s.pitch, s.eye_height, s.sensitivity,
            s.teleport_ack, s.gui_scale, s.frame_counter, s.fov_deg, s.bob_phase, s.bob_amount, 0,
            s.eye_x, s.eye_y, s.eye_z,
            s.tick_qpc, s.prev_x, s.prev_y, s.prev_z, s.cur_x, s.cur_y, s.cur_z,
            s.tick_eye_o, s.tick_eye, s.walk_dist_o, s.walk_dist, s.bob_o, s.bob, s.tick_ms, 0,
            s.camera_mode, s.camera_distance,
        )
        self.mc.end_write(seq)

    def read_mc_state(self) -> Optional[McState]:
        seq = self.mc.try_read()
        if seq is None:
            return None
        v = struct.unpack_from(_MC_FMT, self.buf, OFF_MC_STATE)
        if not self.mc.read_ok(seq):
            return None
        return McState(
            flags=v[1], x=v[2], y=v[3], z=v[4], yaw=v[5], pitch=v[6], eye_height=v[7],
            sensitivity=v[8], teleport_ack=v[9], gui_scale=v[10], frame_counter=v[11],
            fov_deg=v[12], bob_phase=v[13], bob_amount=v[14],
            eye_x=v[16], eye_y=v[17], eye_z=v[18],
            tick_qpc=v[19], prev_x=v[20], prev_y=v[21], prev_z=v[22],
            cur_x=v[23], cur_y=v[24], cur_z=v[25],
            tick_eye_o=v[26], tick_eye=v[27], walk_dist_o=v[28], walk_dist=v[29],
            bob_o=v[30], bob=v[31], tick_ms=v[32],
            camera_mode=v[34], camera_distance=v[35],
        )

    # -- rings -------------------------------------------------------------------------------
    def push_input(self, e: InputEntry) -> bool:
        return self.input_ring.push(struct.pack(_INPUT_FMT, e.type & 0xFFFF, e.code & 0xFFFF, e.value, e.aux, e.time_ms & 0xFFFFFFFF))

    def pop_input(self) -> Optional[InputEntry]:
        rec = self.input_ring.pop()
        if rec is None:
            return None
        t, code, value, aux, tm = struct.unpack(_INPUT_FMT, rec)
        return InputEntry(t, code, value, aux, tm)

    def push_event(self, e: McEvent) -> bool:
        return self.event_ring.push(struct.pack(_EVENT_FMT, e.type & 0xFFFF, e.flags & 0xFFFF, e.actor_id, e.a, e.b, e.c, e.x, e.y, e.z))

    def pop_event(self) -> Optional[McEvent]:
        rec = self.event_ring.pop()
        if rec is None:
            return None
        t, flags, actor_id, a, b, c, x, y, z = struct.unpack(_EVENT_FMT, rec)
        return McEvent(t, flags, actor_id, a, b, c, x, y, z)

    def push_collision(self, rec_bytes: bytes) -> bool:
        return self.collision_ring.push(rec_bytes)

    def pop_collision(self) -> Optional[bytes]:
        return self.collision_ring.pop()

    def pack_collision(self, epoch: int, sec_x: int, sec_y: int, sec_z: int,
                       kind: int, payload: bytes, count: int = 0, flags: int = 0) -> bytes:
        assert len(payload) <= 0x400
        hdr = struct.pack("<IIhhhBBHI", 0, epoch, sec_x, sec_y, sec_z, kind, count & 0xFF, 0, flags)
        hdr += b"\x00" * (0x40 - len(hdr))
        return (hdr + payload.ljust(0x400, b"\x00"))[:COLLISION_REC_BYTES]

    def unpack_collision(self, rec: bytes):
        seq, epoch, sx, sy, sz, kind, count, _pad, flags = struct.unpack_from("<IIhhhBBHI", rec, 0)
        return seq, epoch, (sx, sy, sz), kind, count, flags, rec[0x40:0x440]

    # -- actor table -------------------------------------------------------------------------
    def write_actors(self, entries: list[ActorEntry]) -> None:
        seq = self.actors.begin_write()
        struct.pack_into("<II", self.buf, OFF_ACTOR_TABLE, seq & 0xFFFFFFFF, len(entries))
        for i, e in enumerate(entries[:MAX_ACTORS]):
            struct.pack_into(
                _ACTOR_FMT, self.buf, OFF_ACTOR_TABLE + 0x40 + i * 0x30,
                e.id, e.flags, e.x, e.y, e.z, e.yaw, e.health, e.max_health, e.half_w, e.height, e.extra,
            )
        self.actors.end_write(seq)

    def read_actors(self) -> Optional[list[ActorEntry]]:
        seq = self.actors.try_read()
        if seq is None:
            return None
        _, count = struct.unpack_from("<II", self.buf, OFF_ACTOR_TABLE)
        out = []
        for i in range(min(count, MAX_ACTORS)):
            v = struct.unpack_from(_ACTOR_FMT, self.buf, OFF_ACTOR_TABLE + 0x40 + i * 0x30)
            out.append(ActorEntry(*v))
        if not self.actors.read_ok(seq):
            return None
        return out


def ue_to_mc(x: float, y: float, z: float) -> tuple[float, float, float]:
    """Unreal (Z-up) -> Minecraft (Y-up)."""
    return (x / UNITS_PER_BLOCK, z / UNITS_PER_BLOCK, y / UNITS_PER_BLOCK)


def mc_to_ue(x: float, y: float, z: float) -> tuple[float, float, float]:
    return (x * UNITS_PER_BLOCK, z * UNITS_PER_BLOCK, y * UNITS_PER_BLOCK)


def _wrap_degrees(angle: float) -> float:
    """Normalize an angle to [-180, 180)."""
    return (angle + 180.0) % 360.0 - 180.0


def ue_rotator_to_mc(pitch: float, yaw: float) -> tuple[float, float]:
    """Convert UE3 rotator units to Minecraft ``(yaw, pitch)`` degrees.

    UE yaw zero faces +X and increases toward +Y. With the position mapping above, Minecraft
    yaw -90 faces +X and yaw zero faces +Z, hence the -90 degree offset. UE positive pitch looks
    up while Minecraft positive pitch looks down, so pitch changes sign. UE rotators may arrive
    either signed or as wrapped 16-bit values; normalizing handles both representations.
    """
    units_to_degrees = 360.0 / 65536.0
    mc_yaw = _wrap_degrees(yaw * units_to_degrees - 90.0)
    mc_pitch = _wrap_degrees(-pitch * units_to_degrees)
    return mc_yaw, max(-90.0, min(90.0, mc_pitch))


def mc_rotator_to_ue(yaw: float, pitch: float) -> tuple[int, int]:
    """Convert Minecraft ``(yaw, pitch)`` degrees to UE3 ``(pitch, yaw)`` rotator units."""
    degrees_to_units = 65536.0 / 360.0
    ue_pitch = round(_wrap_degrees(-pitch) * degrees_to_units)
    ue_yaw = round(_wrap_degrees(yaw + 90.0) * degrees_to_units)
    return ue_pitch, ue_yaw
