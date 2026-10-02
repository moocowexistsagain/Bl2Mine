#!/usr/bin/env python3
"""Unit tests for the BorderCraft overlay compositor.

The compositor is the whole Minecraft-side visual: the 3D player model, the mirrored blocks and
the HUD, all drawn with nothing but Canvas rectangles. These tests drive it with fake transport
and a fake Canvas, so they check the behaviour that actually matters in-game - that a skin and a
pose produce a drawable model, that block events land before the next table arrives, and that
the rectangle budget per frame stays sane.
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bl2sdk.BorderCraft import render  # noqa: E402

P = render.P


def checker_skin() -> bytes:
    """A 64x64 BGRA skin with a distinguishable, fully opaque body."""
    pixels = bytearray()
    for y in range(64):
        for x in range(64):
            pixels += bytes((x * 4 % 256, y * 4 % 256, (x + y) * 2 % 256, 255))
    return bytes(pixels)


class FakeOverlay:
    def __init__(self):
        self.frames = []

    def acquire(self):
        return self.frames.pop(0) if self.frames else None


class FakeBridge:
    def __init__(self):
        self.overlay = FakeOverlay()
        self.pose = None
        self.hud = None
        self.blocks = None

    def read_pose(self):
        return self.pose

    def read_hud(self):
        return self.hud

    def read_blocks(self):
        return self.blocks


class FakeCanvas:
    def __init__(self, width=1600, height=900):
        self.ClipX = width
        self.ClipY = height
        self.color = None
        self.pos = None
        self.rects = []

    def SetDrawColor(self, *rgba):
        self.color = rgba

    def SetPos(self, x, y):
        self.pos = (x, y)

    def DrawRect(self, width, height, texture):
        self.rects.append((self.pos[0], self.pos[1], width, height, self.color, texture))


def make_compositor(model_height=64):
    bridge = FakeBridge()
    compositor = render.Compositor(bridge, white_texture=object(), model_height=model_height)
    return bridge, compositor


def push_skin(bridge, frame_id=7, flags=None):
    flags = P.OVERLAY_SKIN if flags is None else flags
    bridge.overlay.frames.append((frame_id, 64, 64, flags, checker_skin()))


class CompositorTransportTests(unittest.TestCase):
    def test_skin_frame_becomes_a_texture_and_invalidates_the_cached_model(self):
        bridge, compositor = make_compositor()
        push_skin(bridge, frame_id=11)
        compositor.poll()
        self.assertIsNotNone(compositor.skin)
        self.assertEqual(compositor.skin.width, 64)
        self.assertEqual(compositor.skin_id, 11)
        self.assertIsNone(compositor._model_key)

    def test_non_skin_overlay_frames_are_ignored(self):
        bridge, compositor = make_compositor()
        bridge.overlay.frames.append((1, 64, 64, 0, checker_skin()))
        compositor.poll()
        self.assertIsNone(compositor.skin)

    def test_pose_hud_and_blocks_are_mirrored_from_the_bridge(self):
        bridge, compositor = make_compositor()
        bridge.pose = P.PoseState(body_yaw=90.0)
        bridge.hud = P.HudState(health=12.0)
        bridge.blocks = ([P.BlockEntry(1, 2, 3, 200, 100, 50, P.BLOCK_FULL_CUBE)], 0xABCD, 4)
        compositor.poll()
        self.assertEqual(compositor.pose.body_yaw, 90.0)
        self.assertEqual(compositor.hud_state.health, 12.0)
        self.assertEqual(compositor.blocks.blocks, {(1, 2, 3): (200, 100, 50, P.BLOCK_FULL_CUBE)})
        self.assertEqual(compositor.blocks.revision, 4)

    def test_a_torn_read_keeps_the_previous_state(self):
        bridge, compositor = make_compositor()
        bridge.pose = P.PoseState(body_yaw=45.0)
        compositor.poll()
        bridge.pose = None  # seqlock reported a write in progress
        compositor.poll()
        self.assertEqual(compositor.pose.body_yaw, 45.0)

    def test_block_events_apply_before_the_next_table_arrives(self):
        _bridge, compositor = make_compositor()
        compositor.on_event(P.McEvent(type=P.EVT_BLOCK_PLACE, actor_id=0xC86432,
                                      flags=P.BLOCK_FULL_CUBE, x=4, y=5, z=6))
        self.assertEqual(compositor.blocks.blocks[(4, 5, 6)], (0xC8, 0x64, 0x32, P.BLOCK_FULL_CUBE))
        compositor.on_event(P.McEvent(type=P.EVT_BLOCK_BREAK, x=4, y=5, z=6))
        self.assertEqual(compositor.blocks.blocks, {})


class CompositorModelTests(unittest.TestCase):
    def test_model_is_rasterized_incrementally_and_then_cached(self):
        bridge, compositor = make_compositor()
        push_skin(bridge)
        bridge.pose = P.PoseState(body_yaw=0.0, limb_swing=2.0, limb_swing_amount=0.8)
        compositor.poll()

        compositor.step_model()
        self.assertTrue(compositor.model.pending,
                        "a whole model must not be rasterized in a single frame")
        steps = 1
        while compositor.model.pending and steps < 400:
            compositor.step_model()
            steps += 1
        self.assertGreater(steps, 1, "the model should take more than one frame to rasterize")
        self.assertTrue(compositor.model.spans, "rasterization produced no spans")

        key = compositor._model_key
        compositor.step_model()
        self.assertEqual(compositor._model_key, key, "an unchanged pose must not re-rasterize")

    def test_a_changed_pose_invalidates_the_cache(self):
        bridge, compositor = make_compositor()
        push_skin(bridge)
        bridge.pose = P.PoseState(body_yaw=0.0)
        compositor.poll()
        compositor.step_model()
        first = compositor._model_key

        bridge.pose = P.PoseState(body_yaw=90.0)
        compositor.poll()
        compositor.step_model()
        self.assertNotEqual(compositor._model_key, first)

    def test_draw_model_places_the_feet_on_the_given_baseline(self):
        bridge, compositor = make_compositor(model_height=48)
        push_skin(bridge)
        bridge.pose = P.PoseState()
        compositor.poll()
        for _ in range(200):
            compositor.step_model()
            if not compositor.model.pending:
                break

        canvas = FakeCanvas()
        draw = compositor.make_draw(canvas, object())
        self.assertTrue(compositor.draw_model(draw, (800.0, 600.0, 96.0)))
        self.assertTrue(canvas.rects)
        xs = [r[0] for r in canvas.rects]
        ys = [r[1] for r in canvas.rects]
        # The avatar is centred on x=800 and no part of it is drawn below the feet line.
        self.assertLess(min(xs), 800.0)
        self.assertGreater(max(xs), 800.0)
        self.assertGreaterEqual(min(ys), 600.0 - 96.0 - 1.0)
        self.assertLessEqual(max(ys), 600.0 + 1.0)

    def test_draw_model_is_a_no_op_without_a_skin(self):
        _bridge, compositor = make_compositor()
        canvas = FakeCanvas()
        draw = compositor.make_draw(canvas, object())
        self.assertFalse(compositor.draw_model(draw, (800.0, 600.0, 96.0)))
        self.assertEqual(canvas.rects, [])


class CompositorFrameTests(unittest.TestCase):
    def _loaded(self):
        bridge, compositor = make_compositor(model_height=56)
        push_skin(bridge)
        bridge.pose = P.PoseState(body_yaw=30.0)
        bridge.hud = P.HudState(
            health=14.0, food=17.0, xp_level=12, xp_progress=0.4, selected_slot=2,
            slots=[P.HudSlot(item_hash=i + 1, count=(i % 64) + 1, rgb=0x40C040 + i,
                             flags=P.SLOT_BLOCK | (P.SLOT_SELECTED if i == 2 else 0),
                             name="Block %d" % i)
                   for i in range(41)],
        )
        bridge.blocks = ([P.BlockEntry(x, 64, z, 180, 170, 160, P.BLOCK_FULL_CUBE)
                          for x in range(-2, 3) for z in range(-2, 3)], 1, 1)
        compositor.poll()
        for _ in range(400):
            compositor.step_model()
            if not compositor.model.pending:
                break
        return compositor

    def test_a_full_frame_draws_blocks_model_and_hud_within_budget(self):
        compositor = self._loaded()
        canvas = FakeCanvas()

        def project(mx, my, mz):
            # A crude pinhole camera looking down -Z from (0, 66, 10); enough to exercise
            # the projection plumbing without pulling Unreal in.
            depth = 10.0 - mz
            if depth <= 0.1:
                return None
            scale = 700.0 / depth
            return (800.0 + mx * scale, 450.0 - (my - 66.0) * scale, depth)

        compositor.on_post_render(canvas, bounds=(300.0, 800.0, 220.0),
                                  camera=(0.0, 66.0, 10.0), project=project)
        self.assertGreater(compositor.rects_last_frame, 0)
        # The whole frame has to fit a Canvas budget Borderlands 2 can afford every frame.
        self.assertLess(compositor.rects_last_frame, 2200,
                        "per-frame rectangle count is over budget")
        self.assertEqual(len(canvas.rects), compositor.rects_last_frame)

    def test_toggles_suppress_their_layer(self):
        compositor = self._loaded()
        canvas = FakeCanvas()
        compositor.show_hud = False
        compositor.show_blocks = False
        compositor.on_post_render(canvas, bounds=(300.0, 800.0, 220.0))
        self.assertGreater(len(canvas.rects), 0)       # the model is still there
        self.assertEqual(compositor.draw_hud(lambda *a: None, (1600, 900)), 0)
        self.assertEqual(compositor.draw_blocks(lambda *a: None, (0, 0, 0), lambda *a: None), 0)

    def test_missing_canvas_or_texture_never_raises(self):
        compositor = self._loaded()
        compositor.on_post_render(None, bounds=(0.0, 0.0, 10.0))
        compositor._white_texture = None
        compositor._texture_lookup_done = True
        compositor.on_post_render(FakeCanvas(), bounds=(0.0, 0.0, 10.0))

    def test_degenerate_canvas_size_is_ignored(self):
        compositor = self._loaded()
        canvas = FakeCanvas(width=0, height=0)
        compositor.on_post_render(canvas, bounds=(0.0, 0.0, 10.0))
        self.assertEqual(canvas.rects, [])

    def test_draw_falls_back_to_drawtile_on_older_canvas_wrappers(self):
        _bridge, compositor = make_compositor()

        class TileOnlyCanvas:
            ClipX, ClipY = 800, 600

            def __init__(self):
                self.tiles = []
                self.pos = (0, 0)
                self.color = None

            def SetDrawColor(self, *rgba):
                self.color = rgba

            def SetPos(self, x, y):
                self.pos = (x, y)

            def DrawTile(self, texture, w, h, u, v, uw, vh):
                self.tiles.append((self.pos, w, h))

        canvas = TileOnlyCanvas()
        draw = compositor.make_draw(canvas, object())
        draw(10, 20, 4, 2, (1, 2, 3, 4))
        draw(10, 20, 0, 2, (1, 2, 3, 4))   # zero width is dropped before touching the Canvas
        self.assertEqual(canvas.tiles, [((10, 20), 4, 2)])


class CameraTests(unittest.TestCase):
    def test_camera_forward_matches_minecraft_yaw_convention(self):
        # Minecraft yaw 0 looks towards +Z (south) and grows clockwise seen from above.
        for yaw, expected in ((0.0, (0.0, 0.0, 1.0)), (90.0, (-1.0, 0.0, 0.0)),
                              (180.0, (0.0, 0.0, -1.0)), (270.0, (1.0, 0.0, 0.0))):
            vector = render.camera_forward(yaw, 0.0)
            for got, want in zip(vector, expected):
                self.assertAlmostEqual(got, want, places=6, msg=f"yaw={yaw}")

    def test_positive_pitch_looks_down(self):
        _x, y, _z = render.camera_forward(0.0, 90.0)
        self.assertAlmostEqual(y, -1.0, places=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
