"""Draw Minecraft inside Borderlands 2's frame.

Unreal Engine 3 gives a Python mod exactly one drawing primitive it can trust: a tinted,
axis-aligned ``Canvas`` tile. Everything Minecraft-shaped that appears in Pandora is built from
that one primitive here:

* :mod:`model3d` rasterizes the **full 3D Minecraft player model** — twelve boxes, the real skin,
  the vanilla biped rig — and this module blits the resulting spans over the projected pawn.
* :mod:`voxel` projects the **blocks the player placed** with Borderlands 2's own camera.
* :mod:`hud` paints Minecraft's **HUD and inventory** (hearts, armour, hunger, XP, hotbar).

Nothing in here decides anything about gameplay. Positions, poses, inventories and block sets
are all computed by Minecraft and read out of shared memory.
"""
from __future__ import annotations

import math

try:
    from . import bordercraft_protocol as P
except ImportError:  # pragma: no cover - development checkout
    import bordercraft_protocol as P

from .hud import HudRenderer
from .model3d import ModelRenderer, pose_key
from .raster import Texture, unpack_rgba
from .voxel import BlockField


def _find_white_texture():
    """Resolve UE3's built-in white tile on current and legacy PythonSDK."""
    try:
        import unrealsdk  # type: ignore

        for attribute in ("find_object", "FindObject"):
            finder = getattr(unrealsdk, attribute, None)
            if finder is not None:
                return finder("Texture2D", "EngineResources.WhiteSquareTexture")
    except Exception:
        pass
    return None


class Compositor:
    """Owns every Minecraft-side visual and draws it with Canvas rectangles."""

    def __init__(self, bridge, white_texture=None, model_height: int = 80):
        self.bridge = bridge
        self.frames_drawn = 0
        self.last_frame_id = 0
        self.skin: Texture | None = None
        self.skin_id = 0
        self.skin_flags = 0
        self.model = ModelRenderer(height=model_height)
        self.hud = HudRenderer()
        self.blocks = BlockField()
        self.pose: P.PoseState | None = None
        self.hud_state: P.HudState | None = None
        self.show_hud = True
        self.show_blocks = True
        self.rects_last_frame = 0
        self._white_texture = white_texture
        self._texture_lookup_done = white_texture is not None
        self._model_key = None

    # -- transport ---------------------------------------------------------------------
    def poll(self):
        """Drain one overlay frame (the skin) plus the pose/HUD/block mirrors."""
        frame = self.bridge.overlay.acquire()
        if frame is not None:
            frame_id, width, height, flags, pixels = frame
            self.last_frame_id = frame_id
            if (flags & P.OVERLAY_SKIN) and width == 64 and height == 64 \
                    and len(pixels) == 64 * 64 * 4:
                self.skin = Texture.from_bgra(width, height, pixels,
                                              bottom_up=bool(flags & P.OVERLAY_BOTTOM_UP))
                self.skin_flags = flags
                self.skin_id = frame_id
                self._model_key = None  # force a redraw with the new skin

        pose = self.bridge.read_pose()
        if pose is not None:
            self.pose = pose
        hud = self.bridge.read_hud()
        if hud is not None:
            self.hud_state = hud
        blocks = self.bridge.read_blocks()
        if blocks is not None:
            entries, world_id, revision = blocks
            self.blocks.update(entries, world_id, revision)
        return frame

    def on_event(self, event: "P.McEvent") -> None:
        """Apply a live Minecraft event (block place/break) before the next table arrives."""
        if event.type in (P.EVT_BLOCK_PLACE, P.EVT_BLOCK_BREAK):
            self.blocks.apply_event(event)

    # -- per-frame work ------------------------------------------------------------------
    def on_engine_frame(self, feet=None) -> None:
        """Game-thread tick: drain transport and advance the amortized model rasterization."""
        self.poll()
        self.step_model()

    def step_model(self, budget: int | None = None) -> None:
        """Rasterize a slice of the player model. Cheap, bounded, never blocks a frame."""
        if self.skin is None or self.pose is None:
            return
        key = pose_key(self.pose, self.skin_id, self.model.height)
        if key != self._model_key:
            if self.model.begin(self.skin, self.pose, key):
                self._model_key = key
        self.model.step(budget)

    # -- drawing -------------------------------------------------------------------------
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

    def make_draw(self, canvas, texture):
        """A ``draw(x, y, w, h, rgba)`` closure over one Canvas."""
        def draw(x, y, width, height, rgba):
            if width <= 0 or height <= 0:
                return
            canvas.SetDrawColor(*rgba)
            canvas.SetPos(x, y)
            draw_rect = getattr(canvas, "DrawRect", None)
            if draw_rect is not None:
                draw_rect(width, height, texture)
            else:
                # Older Willow Canvas wrappers only expose Engine.Canvas.DrawTile.
                canvas.DrawTile(texture, width, height, 0.0, 0.0, 1.0, 1.0)
        return draw

    def draw_model(self, draw, bounds) -> bool:
        """Blit the rasterized 3D player. ``bounds`` is ``(centre x, feet y, pixel height)``."""
        if not self.model.spans:
            return False
        centre_x, feet_y, height = bounds
        buffer_w, buffer_h = self.model.span_size
        scale = height / float(buffer_h)
        if scale <= 0.0:
            return False
        origin_x = centre_x - buffer_w * scale * 0.5
        origin_y = feet_y - buffer_h * scale
        step = scale
        for (x, y, run, color) in self.model.spans:
            r, g, b, a = unpack_rgba(color)
            draw(origin_x + x * scale, origin_y + y * scale,
                 max(1.0, run * scale), max(1.0, step), (r, g, b, a))
        self.frames_drawn += 1
        return True

    def draw_blocks(self, draw, camera, project) -> int:
        """Draw the mirrored Minecraft blocks as 3D cubes in Pandora."""
        if not self.show_blocks or not self.blocks.blocks:
            return 0
        rects = self.blocks.build_rects(camera, project)
        for (x, y, width, height, color) in rects:
            draw(x, y, width, height, color)
        return len(rects)

    def draw_hud(self, draw, viewport) -> int:
        if not self.show_hud or self.hud_state is None:
            return 0
        return self.hud.render(draw, self.hud_state, viewport)

    def on_post_render(self, canvas, bounds=None, camera=None, project=None) -> None:
        """PostRender hook: blocks first (world depth), then the player, then the HUD."""
        self.poll()
        texture = self._texture()
        if canvas is None or texture is None:
            return
        clip_x, clip_y = self._canvas_size(canvas)
        if clip_x <= 0 or clip_y <= 0:
            return
        draw = self.make_draw(canvas, texture)
        count = 0

        if camera is not None and project is not None:
            count += self.draw_blocks(draw, camera, project)
        if bounds is not None:
            before = self.frames_drawn
            if self.draw_model(draw, bounds):
                count += len(self.model.spans)
            del before
        count += self.draw_hud(draw, (clip_x, clip_y))
        self.rects_last_frame = count


# The Phase-1a paper doll has been replaced by the real 3D model; the old name still works.
OverlayCompositor = Compositor


def camera_forward(yaw_degrees: float, pitch_degrees: float):
    """Minecraft-space forward vector for a Minecraft yaw/pitch pair."""
    yaw = math.radians(yaw_degrees)
    pitch = math.radians(pitch_degrees)
    cos_pitch = math.cos(pitch)
    return (-math.sin(yaw) * cos_pitch, -math.sin(pitch), math.cos(yaw) * cos_pitch)
