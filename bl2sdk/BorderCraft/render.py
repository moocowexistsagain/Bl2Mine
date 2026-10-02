"""Minecraft-skin renderer for Borderlands 2's UE3 Canvas.

Minecraft publishes the authenticated player's 64x64 skin through the existing overlay slots.
UE3 cannot accept arbitrary pixel uploads through the supported Python SDK API, so BorderCraft
renders the skin as a deliberately blocky paper doll made from tinted WhiteSquareTexture tiles.
That path is entirely native to UE3, needs no injected D3D9 helper, and works in PostRender.
"""
from __future__ import annotations

import math

try:
    from . import bordercraft_protocol as P
except ImportError:
    import bordercraft_protocol as P


# (destination x/y in skin pixels, source base x/y, source outer-layer x/y, width, height)
# The destination canvas is the familiar 16x32 Minecraft paper-doll silhouette.
_WIDE_PARTS = (
    (4, 20, 4, 20, 4, 36, 4, 12),    # right leg (viewer left)
    (8, 20, 20, 52, 4, 52, 4, 12),   # left leg
    (0, 8, 44, 20, 44, 36, 4, 12),   # right arm
    (12, 8, 36, 52, 52, 52, 4, 12),  # left arm
    (4, 8, 20, 20, 20, 36, 8, 12),   # torso
    (4, 0, 8, 8, 40, 8, 8, 8),       # head + hat
)
_SLIM_PARTS = (
    (4, 20, 4, 20, 4, 36, 4, 12),
    (8, 20, 20, 52, 4, 52, 4, 12),
    (1, 8, 44, 20, 44, 36, 3, 12),
    (12, 8, 36, 52, 52, 52, 3, 12),
    (4, 8, 20, 20, 20, 36, 8, 12),
    (4, 0, 8, 8, 40, 8, 8, 8),
)


def _find_white_texture():
    """Resolve UE3's built-in white tile on current and legacy PythonSDK."""
    try:
        import unrealsdk  # type: ignore

        finder = getattr(unrealsdk, "find_object", None)
        if finder is not None:
            return finder("Texture2D", "EngineResources.WhiteSquareTexture")
        finder = getattr(unrealsdk, "FindObject", None)
        if finder is not None:
            return finder("Texture2D", "EngineResources.WhiteSquareTexture")
    except Exception:
        pass
    return None


class OverlayCompositor:
    """Cache skin payloads and draw an animated Minecraft avatar in BL2."""

    def __init__(self, bridge: P.Bridge, white_texture=None):
        self.bridge = bridge
        self.frames_drawn = 0
        self.last_frame_id = 0
        self.skin_pixels: bytes | None = None
        self.skin_flags = 0
        self.skin_width = 0
        self.skin_height = 0
        self._white_texture = white_texture
        self._texture_lookup_done = white_texture is not None
        self._last_feet: tuple[float, float, float] | None = None
        self._walk_phase = 0.0
        self._move_amount = 0.0

    def poll(self):
        """Acquire and cache the newest Minecraft skin payload, if one is ready."""
        frame = self.bridge.overlay.acquire()
        if frame is None:
            return None
        frame_id, width, height, flags, pixels = frame
        self.last_frame_id = frame_id
        if (flags & P.OVERLAY_SKIN) and width == 64 and height == 64 and len(pixels) == 64 * 64 * 4:
            self.skin_width = width
            self.skin_height = height
            self.skin_flags = flags
            self.skin_pixels = pixels
        return frame

    def update_pose(self, feet: tuple[float, float, float]) -> None:
        """Animate the paper doll from the real BL2 pawn's movement."""
        if self._last_feet is None:
            self._last_feet = feet
            return
        dx = feet[0] - self._last_feet[0]
        dz = feet[2] - self._last_feet[2]
        distance = math.sqrt(dx * dx + dz * dz)
        self._last_feet = feet
        if distance > 0.0005:
            self._walk_phase += min(distance * 10.0, 1.5)
            self._move_amount = min(1.0, distance * 16.0)
        else:
            self._move_amount *= 0.78

    def on_engine_frame(self, feet: tuple[float, float, float] | None = None) -> None:
        """Game-thread tick: drain transport and advance the movement pose."""
        self.poll()
        if feet is not None:
            self.update_pose(feet)

    def _texture(self):
        if not self._texture_lookup_done:
            self._white_texture = _find_white_texture()
            self._texture_lookup_done = True
        return self._white_texture

    @staticmethod
    def _canvas_size(canvas) -> tuple[float, float]:
        try:
            return float(canvas.ClipX), float(canvas.ClipY)
        except (AttributeError, TypeError, ValueError):
            return (0.0, 0.0)

    def _pixel(self, x: int, y: int) -> tuple[int, int, int, int]:
        if self.skin_flags & P.OVERLAY_BOTTOM_UP:
            y = self.skin_height - 1 - y
        i = (y * self.skin_width + x) * 4
        px = self.skin_pixels
        assert px is not None
        return px[i + 2], px[i + 1], px[i], px[i + 3]  # BGRA -> RGBA

    @staticmethod
    def _draw_rect(canvas, texture, x: float, y: float, width: float, height: float, rgba) -> None:
        canvas.SetDrawColor(*rgba)
        canvas.SetPos(x, y)
        draw_rect = getattr(canvas, "DrawRect", None)
        if draw_rect is not None:
            draw_rect(width, height, texture)
        else:
            # Older Willow Canvas wrappers expose only the underlying Engine.Canvas DrawTile.
            canvas.DrawTile(texture, width, height, 0.0, 0.0, 1.0, 1.0)

    def _draw_region(
        self,
        canvas,
        texture,
        origin_x: float,
        origin_y: float,
        scale: int,
        dest_x: int,
        dest_y: int,
        source_x: int,
        source_y: int,
        width: int,
        height: int,
        y_offset: int = 0,
    ) -> None:
        # Coalesce adjacent pixels of exactly the same color. Typical skins render in far fewer
        # Canvas calls than a naïve one-rectangle-per-pixel loop while preserving hard pixel edges.
        for row in range(height):
            col = 0
            while col < width:
                color = self._pixel(source_x + col, source_y + row)
                if color[3] == 0:
                    col += 1
                    continue
                run = 1
                while col + run < width and self._pixel(
                    source_x + col + run, source_y + row
                ) == color:
                    run += 1
                self._draw_rect(
                    canvas,
                    texture,
                    origin_x + (dest_x + col) * scale,
                    origin_y + (dest_y + row + y_offset) * scale,
                    run * scale,
                    scale,
                    color,
                )
                col += run

    def draw_avatar(self, canvas) -> bool:
        """Draw the cached skin at the lower-right of the BL2 viewport."""
        if self.skin_pixels is None or canvas is None:
            return False
        texture = self._texture()
        if texture is None:
            return False
        clip_x, clip_y = self._canvas_size(canvas)
        if clip_x <= 0 or clip_y <= 0:
            return False

        scale = max(2, min(10, int((clip_y * 0.29) / 32.0)))
        avatar_w, avatar_h = 16 * scale, 32 * scale
        margin = max(12, scale * 2)
        origin_x = clip_x - avatar_w - margin
        origin_y = clip_y - avatar_h - margin

        # A translucent plate keeps dark skins readable against Pandora without hiding much HUD.
        self._draw_rect(
            canvas,
            texture,
            origin_x - scale,
            origin_y - scale,
            avatar_w + 2 * scale,
            avatar_h + 2 * scale,
            (0, 0, 0, 105),
        )

        swing = int(round(math.sin(self._walk_phase) * 2.0 * self._move_amount))
        parts = _SLIM_PARTS if self.skin_flags & P.OVERLAY_SLIM else _WIDE_PARTS
        for index, (dx, dy, bx, by, ox, oy, width, height) in enumerate(parts):
            # Legs and arms counter-swing as the real BL2 pawn moves; torso/head stay anchored.
            y_offset = 0
            if index in (0, 3):
                y_offset = swing
            elif index in (1, 2):
                y_offset = -swing
            self._draw_region(
                canvas, texture, origin_x, origin_y, scale,
                dx, dy, bx, by, width, height, y_offset,
            )
            self._draw_region(
                canvas, texture, origin_x, origin_y, scale,
                dx, dy, ox, oy, width, height, y_offset,
            )

        self.frames_drawn += 1
        return True

    def on_post_render(self, canvas) -> None:
        """PostRender hook: acquire a late skin update and draw the current avatar."""
        self.poll()
        self.draw_avatar(canvas)
