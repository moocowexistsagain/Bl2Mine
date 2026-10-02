"""A tiny software rasterizer used to draw real 3D Minecraft geometry inside Borderlands 2.

Borderlands 2 is Unreal Engine 3 and the Python SDK cannot upload an arbitrary texture or submit
a rotated triangle to the renderer: ``Engine.Canvas`` can only draw axis-aligned tiles. So instead
of fighting UE3 we rasterize ourselves.

The pipeline is deliberately boring:

1. transform model-space vertices by the pose matrices, then into view space,
2. project them, clip against the near plane, and rasterize perspective-correct textured
   triangles into a small depth-buffered framebuffer (typically ~48x80 pixels),
3. run-length encode each scanline into coloured spans.

A span is one ``Canvas.DrawRect`` call in Borderlands 2, so a whole 3D Minecraft player costs a
few hundred HUD draws and no native code at all. Everything here is pure stdlib Python so it runs
unchanged inside the game's embedded interpreter and in the test suite.
"""
from __future__ import annotations

import math

INF = float("inf")


def pack_rgba(r: int, g: int, b: int, a: int = 255) -> int:
    return ((a & 0xFF) << 24) | ((r & 0xFF) << 16) | ((g & 0xFF) << 8) | (b & 0xFF)


def unpack_rgba(value: int) -> tuple[int, int, int, int]:
    return ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF, (value >> 24) & 0xFF)


class Texture:
    """An RGBA pixel grid addressed in texel coordinates, with nearest sampling.

    Minecraft skins are pixel art: nearest sampling is not a shortcut, it is the correct filter.
    """

    __slots__ = ("width", "height", "pixels")

    def __init__(self, width: int, height: int, pixels: list[int]):
        if len(pixels) != width * height:
            raise ValueError(f"texture {width}x{height} needs {width * height} pixels, got {len(pixels)}")
        self.width = width
        self.height = height
        self.pixels = pixels

    @classmethod
    def from_bgra(cls, width: int, height: int, data: bytes, bottom_up: bool = False) -> "Texture":
        """Build from the BGRA payload Minecraft publishes through the overlay slots."""
        if len(data) < width * height * 4:
            raise ValueError("BGRA payload too short")
        pixels = [0] * (width * height)
        for y in range(height):
            src_y = (height - 1 - y) if bottom_up else y
            base = src_y * width * 4
            row = y * width
            for x in range(width):
                i = base + x * 4
                pixels[row + x] = ((data[i + 3] << 24) | (data[i + 2] << 16)
                                   | (data[i + 1] << 8) | data[i])
        return cls(width, height, pixels)

    def texel(self, x: int, y: int) -> int:
        if x < 0 or y < 0 or x >= self.width or y >= self.height:
            return 0
        return self.pixels[y * self.width + x]

    def opaque_region(self, u: int, v: int, w: int, h: int) -> bool:
        """True when any texel in the region has a non-zero alpha.

        Skins leave unused overlay regions fully transparent (and some old skins leave them
        opaque black by mistake); this lets the model skip invisible boxes entirely.
        """
        for y in range(v, v + h):
            for x in range(u, u + w):
                if (self.texel(x, y) >> 24) & 0xFF:
                    return True
        return False


class Framebuffer:
    """Colour + depth target. Depth is view-space z; smaller is nearer."""

    __slots__ = ("width", "height", "color", "depth")

    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self.color = [0] * (width * height)
        self.depth = [INF] * (width * height)

    def clear(self) -> None:
        n = self.width * self.height
        self.color = [0] * n
        self.depth = [INF] * n

    def spans(self) -> list[tuple[int, int, int, int]]:
        """Run-length encode into ``(x, y, run, rgba)`` tuples, one Canvas rect each."""
        out = []
        color = self.color
        width = self.width
        for y in range(self.height):
            row = y * width
            x = 0
            while x < width:
                value = color[row + x]
                if value == 0:
                    x += 1
                    continue
                run = 1
                while x + run < width and color[row + x + run] == value:
                    run += 1
                out.append((x, y, run, value))
                x += run
        return out


def shade_rgba(value: int, factor: float) -> int:
    """Multiply the RGB channels of a packed colour, preserving alpha (Minecraft face shading)."""
    if factor >= 0.999:
        return value
    a = (value >> 24) & 0xFF
    r = int(((value >> 16) & 0xFF) * factor)
    g = int(((value >> 8) & 0xFF) * factor)
    b = int((value & 0xFF) * factor)
    return (a << 24) | (r << 16) | (g << 8) | b


def tint_rgba(value: int, rgb: tuple[int, int, int], amount: float) -> int:
    """Blend toward a colour (used for the Minecraft red damage flash)."""
    if amount <= 0.0:
        return value
    amount = min(1.0, amount)
    a = (value >> 24) & 0xFF
    r = int(((value >> 16) & 0xFF) * (1.0 - amount) + rgb[0] * amount)
    g = int(((value >> 8) & 0xFF) * (1.0 - amount) + rgb[1] * amount)
    b = int((value & 0xFF) * (1.0 - amount) + rgb[2] * amount)
    return (a << 24) | (r << 16) | (g << 8) | b


class Rasterizer:
    """Draws projected, textured, depth-buffered triangles into a :class:`Framebuffer`."""

    NEAR = 0.05

    def __init__(self, framebuffer: Framebuffer, focal: float, cx: float, cy: float):
        self.fb = framebuffer
        self.focal = focal
        self.cx = cx
        self.cy = cy

    # -- geometry ---------------------------------------------------------------------------
    def project(self, point: tuple[float, float, float]) -> tuple[float, float, float]:
        """View space -> screen.

        View space is the right-handed world frame rotated onto the camera: ``+x`` is the
        direction the camera's *left* points at and ``z`` is the distance in front of it. Screen
        x therefore decreases as view x grows, which is what keeps a Minecraft skin unmirrored:
        the player's right arm has to land on the viewer's left.
        """
        x, y, z = point
        inv = self.focal / z
        return (self.cx - x * inv, self.cy - y * inv, z)

    def quad(self, corners, uvs, texture: Texture, factor: float = 1.0,
             tint: tuple[tuple[int, int, int], float] | None = None) -> None:
        """Draw a view-space quad (4 corners, 4 texel coordinates) as two triangles."""
        self.triangle(corners[0], corners[1], corners[2], uvs[0], uvs[1], uvs[2], texture, factor, tint)
        self.triangle(corners[0], corners[2], corners[3], uvs[0], uvs[2], uvs[3], texture, factor, tint)

    def triangle(self, a, b, c, uva, uvb, uvc, texture: Texture, factor: float = 1.0,
                 tint: tuple[tuple[int, int, int], float] | None = None) -> None:
        verts = [(a, uva), (b, uvb), (c, uvc)]
        clipped = self._clip_near(verts)
        for i in range(1, len(clipped) - 1):
            self._raster(clipped[0], clipped[i], clipped[i + 1], texture, factor, tint)

    def _clip_near(self, verts):
        """Sutherland-Hodgman against z = NEAR so geometry behind the camera cannot wrap."""
        if all(v[0][2] > self.NEAR for v in verts):
            return verts
        out = []
        count = len(verts)
        for i in range(count):
            cur, nxt = verts[i], verts[(i + 1) % count]
            cur_in = cur[0][2] > self.NEAR
            nxt_in = nxt[0][2] > self.NEAR
            if cur_in:
                out.append(cur)
            if cur_in != nxt_in:
                t = (self.NEAR - cur[0][2]) / (nxt[0][2] - cur[0][2])
                pos = tuple(cur[0][k] + (nxt[0][k] - cur[0][k]) * t for k in range(3))
                uv = tuple(cur[1][k] + (nxt[1][k] - cur[1][k]) * t for k in range(2))
                out.append((pos, uv))
        return out

    def _raster(self, v0, v1, v2, texture: Texture, factor: float,
                tint: tuple[tuple[int, int, int], float] | None) -> None:
        fb = self.fb
        width, height = fb.width, fb.height
        p0 = self.project(v0[0])
        p1 = self.project(v1[0])
        p2 = self.project(v2[0])

        area = (p1[0] - p0[0]) * (p2[1] - p0[1]) - (p1[1] - p0[1]) * (p2[0] - p0[0])
        if area == 0.0:
            return
        # Backfaces are skipped: every box is closed, so the far side is never needed and the
        # depth buffer stays free for the overlay (hat/jacket) layers.
        if area < 0.0:
            return
        inv_area = 1.0 / area

        min_x = max(0, int(math.floor(min(p0[0], p1[0], p2[0]))))
        max_x = min(width - 1, int(math.ceil(max(p0[0], p1[0], p2[0]))))
        min_y = max(0, int(math.floor(min(p0[1], p1[1], p2[1]))))
        max_y = min(height - 1, int(math.ceil(max(p0[1], p1[1], p2[1]))))
        if min_x > max_x or min_y > max_y:
            return

        iz0, iz1, iz2 = 1.0 / p0[2], 1.0 / p1[2], 1.0 / p2[2]
        u0, v0t = v0[1][0] * iz0, v0[1][1] * iz0
        u1, v1t = v1[1][0] * iz1, v1[1][1] * iz1
        u2, v2t = v2[1][0] * iz2, v2[1][1] * iz2

        color = fb.color
        depth = fb.depth
        tex_w = texture.width
        tex_h = texture.height
        texels = texture.pixels

        for py in range(min_y, max_y + 1):
            sy = py + 0.5
            row = py * width
            for px in range(min_x, max_x + 1):
                sx = px + 0.5
                w0 = ((p2[0] - p1[0]) * (sy - p1[1]) - (p2[1] - p1[1]) * (sx - p1[0])) * inv_area
                if w0 < 0.0:
                    continue
                w1 = ((p0[0] - p2[0]) * (sy - p2[1]) - (p0[1] - p2[1]) * (sx - p2[0])) * inv_area
                if w1 < 0.0:
                    continue
                w2 = 1.0 - w0 - w1
                if w2 < 0.0:
                    continue

                inv_z = w0 * iz0 + w1 * iz1 + w2 * iz2
                if inv_z <= 0.0:
                    continue
                z = 1.0 / inv_z
                index = row + px
                if z >= depth[index]:
                    continue

                u = (w0 * u0 + w1 * u1 + w2 * u2) * z
                v = (w0 * v0t + w1 * v1t + w2 * v2t) * z
                tx = int(u)
                ty = int(v)
                if tx < 0:
                    tx = 0
                elif tx >= tex_w:
                    tx = tex_w - 1
                if ty < 0:
                    ty = 0
                elif ty >= tex_h:
                    ty = tex_h - 1
                texel = texels[ty * tex_w + tx]
                if (texel >> 24) & 0xFF < 16:
                    continue  # transparent skin pixels must not claim the depth buffer
                shaded = shade_rgba(texel, factor)
                if tint is not None:
                    shaded = tint_rgba(shaded, tint[0], tint[1])
                color[index] = shaded
                depth[index] = z

    def solid_quad(self, corners, rgba: int, factor: float = 1.0) -> None:
        """Depth-buffered flat-coloured quad (used for voxel block faces)."""
        self._solid_tri(corners[0], corners[1], corners[2], rgba, factor)
        self._solid_tri(corners[0], corners[2], corners[3], rgba, factor)

    def _solid_tri(self, a, b, c, rgba: int, factor: float) -> None:
        verts = self._clip_near([(a, (0.0, 0.0)), (b, (0.0, 0.0)), (c, (0.0, 0.0))])
        if len(verts) < 3:
            return
        shaded = shade_rgba(rgba, factor)
        fb = self.fb
        width, height = fb.width, fb.height
        color, depth = fb.color, fb.depth
        for i in range(1, len(verts) - 1):
            p0 = self.project(verts[0][0])
            p1 = self.project(verts[i][0])
            p2 = self.project(verts[i + 1][0])
            area = (p1[0] - p0[0]) * (p2[1] - p0[1]) - (p1[1] - p0[1]) * (p2[0] - p0[0])
            if area <= 0.0:
                continue
            inv_area = 1.0 / area
            min_x = max(0, int(math.floor(min(p0[0], p1[0], p2[0]))))
            max_x = min(width - 1, int(math.ceil(max(p0[0], p1[0], p2[0]))))
            min_y = max(0, int(math.floor(min(p0[1], p1[1], p2[1]))))
            max_y = min(height - 1, int(math.ceil(max(p0[1], p1[1], p2[1]))))
            iz0, iz1, iz2 = 1.0 / p0[2], 1.0 / p1[2], 1.0 / p2[2]
            for py in range(min_y, max_y + 1):
                sy = py + 0.5
                row = py * width
                for px in range(min_x, max_x + 1):
                    sx = px + 0.5
                    w0 = ((p2[0] - p1[0]) * (sy - p1[1]) - (p2[1] - p1[1]) * (sx - p1[0])) * inv_area
                    if w0 < 0.0:
                        continue
                    w1 = ((p0[0] - p2[0]) * (sy - p2[1]) - (p0[1] - p2[1]) * (sx - p2[0])) * inv_area
                    if w1 < 0.0:
                        continue
                    w2 = 1.0 - w0 - w1
                    if w2 < 0.0:
                        continue
                    inv_z = w0 * iz0 + w1 * iz1 + w2 * iz2
                    if inv_z <= 0.0:
                        continue
                    z = 1.0 / inv_z
                    index = row + px
                    if z < depth[index]:
                        color[index] = shaded
                        depth[index] = z


# -- small matrix helpers ----------------------------------------------------------------------
def rotation_matrix(pitch: float, yaw: float, roll: float):
    """Right-handed ZYX rotation (radians), matching Minecraft's part rotation order."""
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    cr, sr = math.cos(roll), math.sin(roll)
    # R = Ry(yaw) * Rx(pitch) * Rz(roll)
    return (
        (cy * cr + sy * sp * sr, -cy * sr + sy * sp * cr, sy * cp),
        (cp * sr, cp * cr, -sp),
        (-sy * cr + cy * sp * sr, sy * sr + cy * sp * cr, cy * cp),
    )


def mat_mul(a, b):
    return tuple(
        tuple(sum(a[r][k] * b[k][c] for k in range(3)) for c in range(3))
        for r in range(3)
    )


def mat_apply(m, v):
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


IDENTITY = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
