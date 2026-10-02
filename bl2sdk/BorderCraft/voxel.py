"""Draw the blocks the player placed in Minecraft as real 3D cubes inside Borderlands 2.

Minecraft owns the blocks: placing and breaking happen in the Minecraft world with Minecraft's
own rules, and the Fabric mod mirrors the resulting block set through the shared memory
``BlockTable``. This module is purely a renderer for that mirror.

Each block is projected with Borderlands 2's own camera (``Canvas.Project``), so cubes sit in
Pandora with correct perspective and parallax. The visible faces are filled with horizontal
Canvas bands — the same trick the player model uses — and shaded with Minecraft's directional
face brightness so a stack of cobblestone reads as Minecraft, not as a flat decal.

Everything here takes an injected ``project`` callable, which keeps it testable without the game.
"""
from __future__ import annotations

try:
    from . import bordercraft_protocol as P
except ImportError:  # pragma: no cover - development checkout
    import bordercraft_protocol as P

# Minecraft's directional shading, matching model3d.
SHADE = {
    "top": 1.0,
    "bottom": 0.5,
    "north": 0.8,
    "south": 0.8,
    "east": 0.6,
    "west": 0.6,
}

# Unit-cube corner offsets, indexed as (x, y, z) bits.
_CORNERS = [(x, y, z) for x in (0, 1) for y in (0, 1) for z in (0, 1)]
_CORNER_INDEX = {c: i for i, c in enumerate(_CORNERS)}

# face -> (outward axis, sign, the four corners in order)
_FACES = (
    ("top", 1, 1, ((0, 1, 0), (1, 1, 0), (1, 1, 1), (0, 1, 1))),
    ("bottom", 1, -1, ((0, 0, 1), (1, 0, 1), (1, 0, 0), (0, 0, 0))),
    ("north", 2, -1, ((0, 1, 0), (0, 0, 0), (1, 0, 0), (1, 1, 0))),
    ("south", 2, 1, ((1, 1, 1), (1, 0, 1), (0, 0, 1), (0, 1, 1))),
    ("west", 0, -1, ((0, 1, 1), (0, 0, 1), (0, 0, 0), (0, 1, 0))),
    ("east", 0, 1, ((1, 1, 0), (1, 0, 0), (1, 0, 1), (1, 1, 1))),
)


class BlockField:
    """The mirrored set of Minecraft blocks, plus the drawing of them."""

    def __init__(self, max_distance: float = 24.0, max_blocks: int = 96,
                 band_pixels: int = 4, max_rects: int = 420):
        self.blocks: dict[tuple[int, int, int], tuple[int, int, int, int]] = {}
        self.revision = -1
        self.world_id = 0
        self.max_distance = float(max_distance)
        self.max_blocks = int(max_blocks)
        self.band_pixels = max(1, int(band_pixels))
        self.max_rects = int(max_rects)
        self.rects_drawn = 0
        self.blocks_drawn = 0

    # -- mirror -------------------------------------------------------------------------
    def update(self, entries, world_id: int = 0, revision: int = 0) -> bool:
        """Replace the mirrored set. Returns True when anything actually changed."""
        if revision == self.revision and world_id == self.world_id:
            return False
        self.blocks = {(e.x, e.y, e.z): (e.r, e.g, e.b, e.flags) for e in entries}
        self.revision = revision
        self.world_id = world_id
        return True

    def apply_event(self, event: "P.McEvent") -> None:
        """Fold a live block place/break event in before the next full table arrives."""
        pos = (int(round(event.x)), int(round(event.y)), int(round(event.z)))
        if event.type == P.EVT_BLOCK_PLACE:
            rgb = event.actor_id
            self.blocks[pos] = ((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF,
                                event.flags & 0xFF)
        elif event.type == P.EVT_BLOCK_BREAK:
            self.blocks.pop(pos, None)

    # -- drawing ------------------------------------------------------------------------
    def visible(self, camera: tuple[float, float, float]):
        """Blocks within range, sorted far to near (painter's algorithm)."""
        cx, cy, cz = camera
        limit = self.max_distance * self.max_distance
        scored = []
        for (x, y, z), color in self.blocks.items():
            dx = x + 0.5 - cx
            dy = y + 0.5 - cy
            dz = z + 0.5 - cz
            d2 = dx * dx + dy * dy + dz * dz
            if d2 <= limit:
                scored.append((d2, (x, y, z), color))
        scored.sort(key=lambda item: -item[0])
        if len(scored) > self.max_blocks:
            scored = scored[-self.max_blocks:]
        return scored

    def build_rects(self, camera, project, occluders: set | None = None):
        """Return ``[(x, y, w, h, (r, g, b, a)), ...]`` ready for ``Canvas.DrawRect``.

        ``project`` maps a Minecraft-space point to ``(screen_x, screen_y, depth)`` or ``None``
        when the point is behind the camera. ``occluders`` lets the caller suppress faces that
        are buried between neighbouring blocks.
        """
        occluders = self.blocks if occluders is None else occluders
        rects: list[tuple[int, int, int, int, tuple[int, int, int, int]]] = []
        self.blocks_drawn = 0
        for _d2, (bx, by, bz), (r, g, b, flags) in self.visible(camera):
            if len(rects) >= self.max_rects:
                break
            projected = []
            ok = True
            for (ox, oy, oz) in _CORNERS:
                point = project(bx + ox, by + oy, bz + oz)
                if point is None:
                    ok = False
                    break
                projected.append(point)
            if not ok:
                continue
            alpha = 170 if (flags & P.BLOCK_TRANSLUCENT) else 255
            drew = False
            for name, axis, sign, corners in _FACES:
                neighbour = [bx, by, bz]
                neighbour[axis] += sign
                if tuple(neighbour) in occluders:
                    continue  # hidden between two blocks
                if not self._front_facing(axis, sign, (bx, by, bz), camera):
                    continue
                quad = [projected[_CORNER_INDEX[c]] for c in corners]
                shade = SHADE[name]
                color = (int(r * shade), int(g * shade), int(b * shade), alpha)
                before = len(rects)
                self._fill_quad(quad, color, rects)
                drew = drew or len(rects) > before
                if len(rects) >= self.max_rects:
                    break
            if drew:
                self.blocks_drawn += 1
        self.rects_drawn = len(rects)
        return rects

    @staticmethod
    def _front_facing(axis: int, sign: int, block, camera) -> bool:
        centre = block[axis] + 0.5 + sign * 0.5
        return (camera[axis] - centre) * sign > 0.0

    def _fill_quad(self, quad, color, out) -> None:
        """Scanline-fill a convex screen-space quad as horizontal Canvas bands."""
        ys = [p[1] for p in quad]
        xs = [p[0] for p in quad]
        top = min(ys)
        bottom = max(ys)
        height = bottom - top
        if height < 0.6 or (max(xs) - min(xs)) < 0.6:
            return
        band = self.band_pixels
        if height / band > 64:
            band = height / 64.0  # never explode on a block that fills the screen
        y = top
        while y < bottom:
            y_next = min(bottom, y + band)
            mid = (y + y_next) * 0.5
            span = _scan_x(quad, mid)
            if span is not None:
                x0, x1 = span
                width = x1 - x0
                if width >= 0.5:
                    out.append((int(x0), int(y), max(1, int(width + 0.5)),
                                max(1, int(y_next - y + 0.5)), color))
            if len(out) >= self.max_rects:
                return
            y = y_next


def _scan_x(quad, y):
    """Intersect a convex polygon with a horizontal line; returns ``(x_min, x_max)``."""
    xs = []
    count = len(quad)
    for i in range(count):
        ax, ay = quad[i][0], quad[i][1]
        bx, by = quad[(i + 1) % count][0], quad[(i + 1) % count][1]
        if ay == by:
            continue
        if (ay <= y < by) or (by <= y < ay):
            t = (y - ay) / (by - ay)
            xs.append(ax + (bx - ax) * t)
    if len(xs) < 2:
        return None
    return (min(xs), max(xs))


def make_canvas_projector(canvas, make_vector, units_per_block: float = P.UNITS_PER_BLOCK,
                          camera_forward=None, camera_pos=None, max_depth: float = 4000.0):
    """Build a ``project`` callable backed by Borderlands 2's ``Canvas.Project``.

    ``Canvas.Project`` has no defined behaviour for points behind the camera, so when the caller
    supplies the camera position and forward vector (both in Minecraft space) we reject those
    points before UE3 ever sees them.
    """
    def project(mx, my, mz):
        if camera_forward is not None and camera_pos is not None:
            dx = mx - camera_pos[0]
            dy = my - camera_pos[1]
            dz = mz - camera_pos[2]
            depth = dx * camera_forward[0] + dy * camera_forward[1] + dz * camera_forward[2]
            if depth <= 0.2 or depth > max_depth:
                return None
        else:
            depth = 1.0
        ux, uy, uz = P.mc_to_ue(mx, my, mz)
        try:
            screen = canvas.Project(make_vector(ux, uy, uz))
            return (float(screen.X), float(screen.Y), depth)
        except Exception:
            return None

    return project
