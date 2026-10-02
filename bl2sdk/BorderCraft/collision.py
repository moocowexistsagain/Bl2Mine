"""Export Pandora's collision so Minecraft's physics can stand on it.

Minecraft stays authoritative for movement. For that to mean anything, Minecraft's collision
solver has to know the shape of the Borderlands 2 world, so this module runs Unreal line traces
on a grid around the player, turns the hits into the heightfield records of
``protocol/bordercraft_protocol.h`` (1/8-block vertical resolution, 16x16 columns per section),
and streams them over the collision ring. The Fabric side feeds them into its ``CollisionField``,
and Minecraft's own ``Entity`` collision code does the rest — gravity, step-up, sprint jumping,
sneaking at edges, all unchanged.

Tracing a whole neighbourhood in one frame would stall the game, so :class:`CollisionExporter`
walks a ring of sections around the player a few columns at a time and re-visits a section only
when the player has moved or the map changed.

The Unreal calls are injected (``trace``), which keeps the scheduling and packing logic testable
without the game.
"""
from __future__ import annotations

import math
import struct

try:
    from . import bordercraft_protocol as P
except ImportError:  # pragma: no cover - development checkout
    import bordercraft_protocol as P

SECTION = 16                 # blocks per section edge, as in Minecraft
SUB = 8                      # heightfield sub-block resolution (1/8 block)
NO_HEIGHT = -32768           # sentinel for "nothing solid in this column"
TRACE_TOP = 48.0             # blocks above the player where a column trace starts
TRACE_BOTTOM = 32.0          # blocks below the player where it ends
WALKABLE_COS = math.cos(math.radians(50.0))  # steeper than this is a wall, not a floor


def pack_heightfield(columns) -> bytes:
    """Pack 256 ``(height_in_eighths, flags)`` columns into a collision record payload."""
    if len(columns) != SECTION * SECTION:
        raise ValueError(f"expected {SECTION * SECTION} columns, got {len(columns)}")
    out = bytearray()
    for height, flags in columns:
        height = max(-32768, min(32767, int(height)))
        out += struct.pack("<hBB", height, flags & 0xFF, 0)
    return bytes(out)


def unpack_heightfield(payload: bytes):
    """Inverse of :func:`pack_heightfield` (used by the tests and the self-test)."""
    return [struct.unpack_from("<hBB", payload, i * 4)[:2] for i in range(SECTION * SECTION)]


def surface_flags(normal_y: float | None, water: bool = False) -> int:
    """Classify a traced surface the way the protocol's ``ColFlags`` expect."""
    flags = 0
    if water:
        flags |= P.COL_WATER
    if normal_y is None:
        flags |= P.COL_WALKABLE
        return flags
    if normal_y >= WALKABLE_COS:
        flags |= P.COL_WALKABLE
    else:
        flags |= P.COL_STEEP
    return flags


def section_ring(center, radius: int):
    """Section coordinates around ``center``, nearest first, as a flat list."""
    cx, cy, cz = center
    out = []
    for dx in range(-radius, radius + 1):
        for dz in range(-radius, radius + 1):
            out.append((cx + dx, cy, cz + dz, dx * dx + dz * dz))
    out.sort(key=lambda item: item[3])
    return [(x, y, z) for x, y, z, _ in out]


class CollisionExporter:
    """Streams Borderlands 2 collision to Minecraft, amortized over frames.

    ``trace(x, z, top_y, bottom_y)`` must return ``None`` (nothing hit) or
    ``(hit_y, normal_y, is_water)`` in Minecraft coordinates — one downward line trace.
    """

    def __init__(self, trace, radius: int = 3, columns_per_tick: int = 64,
                 refresh_distance: float = 6.0):
        self.trace = trace
        self.radius = int(radius)
        self.columns_per_tick = max(1, int(columns_per_tick))
        self.refresh_distance = float(refresh_distance)
        self.epoch = 0
        self.records_built = 0
        self.columns_traced = 0
        self._queue: list[tuple[int, int, int]] = []
        self._partial: dict[tuple[int, int, int], list] = {}
        self._cursor = 0
        self._done: set[tuple[int, int, int]] = set()
        self._origin: tuple[float, float, float] | None = None

    # -- scheduling -------------------------------------------------------------------------
    def reset(self, epoch: int) -> None:
        """A new map (or a teleport): everything Minecraft holds is stale."""
        self.epoch = epoch
        self._queue = []
        self._partial = {}
        self._cursor = 0
        self._done = set()
        self._origin = None

    def player_moved(self, position) -> bool:
        if self._origin is None:
            return True
        dx = position[0] - self._origin[0]
        dy = position[1] - self._origin[1]
        dz = position[2] - self._origin[2]
        return (dx * dx + dz * dz) > self.refresh_distance ** 2 or abs(dy) > self.refresh_distance

    def schedule(self, position) -> None:
        """Queue the sections around ``position`` that are not already published."""
        self._origin = position
        centre = (int(math.floor(position[0] / SECTION)),
                  int(math.floor(position[1] / SECTION)),
                  int(math.floor(position[2] / SECTION)))
        wanted = section_ring(centre, self.radius)
        self._queue = [s for s in wanted if s not in self._done]
        self._cursor = 0

    # -- production -------------------------------------------------------------------------
    def step(self, position, budget: int | None = None) -> list[bytes]:
        """Trace up to ``budget`` columns and return any finished collision records."""
        if self.player_moved(position):
            self.schedule(position)
        if not self._queue:
            return []
        budget = self.columns_per_tick if budget is None else max(1, int(budget))
        produced: list[bytes] = []
        traced = 0
        while self._queue and traced < budget:
            section = self._queue[0]
            columns = self._partial.get(section)
            if columns is None:
                columns = []
                self._partial[section] = columns
            base_x = section[0] * SECTION
            base_z = section[2] * SECTION
            while len(columns) < SECTION * SECTION and traced < budget:
                index = len(columns)
                local_z, local_x = divmod(index, SECTION)
                world_x = base_x + local_x + 0.5
                world_z = base_z + local_z + 0.5
                top = position[1] + TRACE_TOP
                bottom = position[1] - TRACE_BOTTOM
                hit = self.trace(world_x, world_z, top, bottom)
                traced += 1
                self.columns_traced += 1
                if hit is None:
                    columns.append((NO_HEIGHT, 0))
                else:
                    hit_y, normal_y, water = hit
                    columns.append((int(round(hit_y * SUB)), surface_flags(normal_y, water)))
            if len(columns) == SECTION * SECTION:
                produced.append(self._finish(section, columns))
        return produced

    def _finish(self, section, columns) -> bytes:
        self._partial.pop(section, None)
        self._queue.pop(0)
        self._done.add(section)
        self.records_built += 1
        payload = pack_heightfield(columns)
        header = struct.pack("<IIhhhBBHI", 0, self.epoch, section[0], section[1], section[2],
                             P.COL_HEIGHTFIELD, 0, 0, 0)
        header += b"\x00" * (0x40 - len(header))
        return (header + payload.ljust(0x400, b"\x00"))[:P.COLLISION_REC_BYTES]


def make_unreal_tracer(pc, make_vector, units_per_block: float = P.UNITS_PER_BLOCK):
    """Build a ``trace`` callable backed by ``Actor.Trace`` on the Borderlands 2 side.

    ``Actor.Trace`` returns the hit actor plus out-parameters for location and normal. Any SDK
    or engine hiccup degrades to "nothing here", which simply leaves that column empty rather
    than dropping the player through the world: Minecraft keeps whatever it had.
    """
    def trace(mc_x, mc_z, top_y, bottom_y):
        try:
            start = P.mc_to_ue(mc_x, top_y, mc_z)
            end = P.mc_to_ue(mc_x, bottom_y, mc_z)
            result = pc.Trace(make_vector(*end), make_vector(*start), True)
            if not result or result[0] is None:
                return None
            location = result[1]
            normal = result[2] if len(result) > 2 else None
            hit = P.ue_to_mc(float(location.X), float(location.Y), float(location.Z))
            normal_y = None
            if normal is not None:
                try:
                    normal_y = float(normal.Z)  # UE3 is Z-up: Z is the Minecraft Y axis
                except (AttributeError, TypeError, ValueError):
                    normal_y = None
            return (hit[1], normal_y, False)
        except Exception:
            return None

    return trace
