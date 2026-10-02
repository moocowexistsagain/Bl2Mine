#!/usr/bin/env python3
"""Unit tests for the 3D Minecraft player model rasterized in Borderlands 2.

The avatar in Pandora is the real Minecraft model: the same box layout, the same skin UV unwrap
and the same walk cycle Mojang's BipedEntityModel uses. These tests pin the parts that are easy
to get subtly, invisibly wrong - the UV unwrap, which way the model faces, which side is the
player's right, the sneak and swing animations, and the pose cache quantization.
"""
from __future__ import annotations

import math
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bl2sdk.BorderCraft import model3d  # noqa: E402
from bl2sdk.BorderCraft.raster import Texture, pack_rgba  # noqa: E402

P = model3d.P


def solid_skin(colour=(200, 120, 60)) -> Texture:
    r, g, b = colour
    return Texture(64, 64, [pack_rgba(r, g, b, 255)] * (64 * 64))


def tagged_skin() -> Texture:
    """A skin where every texel encodes its own (u, v), so a render reveals which texels ran."""
    pixels = []
    for v in range(64):
        for u in range(64):
            pixels.append(pack_rgba(u * 4, v * 4, 255, 255))
    return Texture(64, 64, pixels)


def blocky_skin() -> Texture:
    """A skin with large flat regions, like a real one: run-length coalescing can do its job."""
    pixels = []
    for v in range(64):
        for u in range(64):
            cell = (u // 8) * 7 + (v // 8) * 13
            pixels.append(pack_rgba(40 + cell * 3 % 200, 60 + cell * 5 % 180,
                                    90 + cell * 11 % 160, 255))
    return Texture(64, 64, pixels)


class BoxGeometryTests(unittest.TestCase):
    def test_wide_and_slim_arms_differ_only_in_width(self):
        wide = {box.name: box for box in model3d.build_boxes(False)}
        slim = {box.name: box for box in model3d.build_boxes(True)}
        self.assertEqual(wide.keys(), slim.keys())
        self.assertEqual(wide["right_arm"].size[0], 4)
        self.assertEqual(slim["right_arm"].size[0], 3)
        self.assertEqual(wide["body"].size, slim["body"].size)

    def test_every_box_has_six_faces_with_in_range_uvs(self):
        for box in model3d.build_boxes(False) + model3d.build_boxes(True):
            faces = list(box.faces())
            self.assertEqual(len(faces), 6, box.name)
            names = {name for name, _corners, _uvs in faces}
            self.assertEqual(names, {"top", "bottom", "left", "right", "front", "back"})
            for _name, corners, uvs in faces:
                self.assertEqual(len(corners), 4)
                self.assertEqual(len(uvs), 4)
                for u, v in uvs:
                    self.assertGreaterEqual(u, 0, box.name)
                    self.assertGreaterEqual(v, 0, box.name)
                    self.assertLessEqual(u, 64, box.name)
                    self.assertLessEqual(v, 64, box.name)

    def test_head_front_face_uses_the_vanilla_face_region(self):
        head = next(b for b in model3d.build_boxes(False) if b.name == "head")
        faces = {name: uvs for name, _corners, uvs in head.faces()}
        # Vanilla's head unwrap: the face is the 8x8 tile at (8, 8), the back of the head
        # is the 8x8 tile at (24, 8).
        for name, (u0, v0) in (("front", (8, 8)), ("back", (24, 8))):
            us = [u for u, _v in faces[name]]
            vs = [v for _u, v in faces[name]]
            self.assertAlmostEqual(min(us), u0, places=2, msg=name)
            self.assertAlmostEqual(max(us), u0 + 8, places=2, msg=name)
            self.assertAlmostEqual(min(vs), v0, places=2, msg=name)
            self.assertAlmostEqual(max(vs), v0 + 8, places=2, msg=name)

    def test_hat_layer_sits_in_the_second_half_of_the_skin(self):
        hat = next(b for b in model3d.build_boxes(False) if b.name == "hat")
        self.assertTrue(hat.overlay)
        self.assertEqual(hat.uv, (32, 0))


class RigTests(unittest.TestCase):
    def test_a_neutral_pose_leaves_every_part_unrotated(self):
        rig = model3d.evaluate_rig(P.PoseState())
        for name, (_pivot, matrix) in rig.parts.items():
            for row in range(3):
                for col in range(3):
                    expected = 1.0 if row == col else 0.0
                    self.assertAlmostEqual(matrix[row][col], expected, places=6,
                                           msg=f"{name} r{row}c{col}")

    def test_walking_swings_the_arms_and_legs_in_opposition(self):
        pose = P.PoseState(limb_swing=1.0, limb_swing_amount=1.0)
        rig = model3d.evaluate_rig(pose)
        # Vanilla: right arm and left leg move together, opposite the left arm and right leg.
        right_arm = rig.parts["right_arm"][1][1][2]
        left_arm = rig.parts["left_arm"][1][1][2]
        right_leg = rig.parts["right_leg"][1][1][2]
        left_leg = rig.parts["left_leg"][1][1][2]
        self.assertGreater(abs(right_arm), 1e-6)
        self.assertAlmostEqual(right_arm, -left_arm, places=6)
        self.assertAlmostEqual(right_leg, -left_leg, places=6)
        self.assertGreater(right_arm * left_leg, 0.0)

    def test_limb_swing_amount_zero_freezes_the_walk_cycle(self):
        rig = model3d.evaluate_rig(P.PoseState(limb_swing=3.7, limb_swing_amount=0.0))
        self.assertAlmostEqual(rig.parts["right_leg"][1][1][2], 0.0, places=6)

    def test_sneaking_pitches_the_body_and_lowers_the_model(self):
        standing = model3d.evaluate_rig(P.PoseState())
        sneaking = model3d.evaluate_rig(P.PoseState(sneak_amount=1.0, flags=P.POSE_SNEAKING))
        self.assertLess(sneaking.offset[1], standing.offset[1])
        self.assertNotAlmostEqual(sneaking.parts["body"][1][1][2], 0.0, places=6)

    def test_head_yaw_is_clamped_the_way_vanilla_clamps_it(self):
        extreme = model3d.evaluate_rig(P.PoseState(head_yaw=170.0))
        clamped = model3d.evaluate_rig(P.PoseState(head_yaw=75.0))
        for row in range(3):
            for col in range(3):
                self.assertAlmostEqual(extreme.parts["head"][1][row][col],
                                       clamped.parts["head"][1][row][col], places=6)

    def test_slim_flag_selects_the_three_pixel_arm(self):
        self.assertTrue(model3d.evaluate_rig(P.PoseState(flags=P.POSE_SLIM)).slim)
        self.assertFalse(model3d.evaluate_rig(P.PoseState()).slim)


class RenderTests(unittest.TestCase):
    def test_rendering_produces_spans_that_fit_the_buffer(self):
        renderer = model3d.ModelRenderer(height=64)
        spans = renderer.render_now(solid_skin(), P.PoseState())
        self.assertTrue(spans)
        width, height = renderer.span_size
        for x, y, run, _colour in spans:
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x + run, width)
            self.assertLess(y, height)

    def test_the_model_stands_on_the_bottom_edge_of_the_buffer(self):
        renderer = model3d.ModelRenderer(height=96)
        spans = renderer.render_now(solid_skin(), P.PoseState())
        _width, height = renderer.span_size
        lowest = max(y for _x, y, _run, _colour in spans)
        self.assertGreaterEqual(lowest, height - 3,
                                "the feet must reach the bottom of the frame")

    def test_a_facing_player_shows_their_face_and_a_turned_one_does_not(self):
        # The 8x8 face tile is painted pure red and the rest of the skin pure blue. Face
        # shading only scales channels, so a zero channel stays zero and the two are
        # impossible to confuse however the model is lit.
        pixels = []
        for v in range(64):
            for u in range(64):
                face = 8 <= u < 16 and 8 <= v < 16
                pixels.append(pack_rgba(255, 0, 0, 255) if face else pack_rgba(0, 0, 255, 255))
        skin = Texture(64, 64, pixels)
        renderer = model3d.ModelRenderer(height=96)

        def face_pixels(body_yaw):
            spans = renderer.render_now(skin, P.PoseState(body_yaw=body_yaw))
            return sum(run for _x, _y, run, c in spans
                       if ((c >> 16) & 0xFF) > 0 and ((c >> 8) & 0xFF) == 0 and (c & 0xFF) == 0)

        # Minecraft yaw is relative to the Borderlands 2 camera here: 180 means the player has
        # turned around to look at it, 0 means the camera is behind them.
        self.assertGreater(face_pixels(180.0), 20)
        self.assertEqual(face_pixels(0.0), 0, "the camera behind the player must see the back")

    def test_stepping_is_amortized_and_only_swaps_in_a_complete_image(self):
        renderer = model3d.ModelRenderer(height=80, faces_per_step=4)
        self.assertTrue(renderer.begin(solid_skin(), P.PoseState(), key=("a",)))
        self.assertTrue(renderer.pending)
        self.assertEqual(renderer.spans, [])
        steps = 0
        while renderer.pending and steps < 1000:
            renderer.step()
            steps += 1
        self.assertGreater(steps, 4)
        self.assertTrue(renderer.spans)
        self.assertEqual(renderer.renders_completed, 1)
        self.assertFalse(renderer.begin(solid_skin(), P.PoseState(), key=("a",)))

    def test_resize_rebuilds_the_buffer(self):
        renderer = model3d.ModelRenderer(height=48)
        renderer.render_now(solid_skin(), P.PoseState())
        renderer.resize(72)
        self.assertEqual(renderer.height, 72)
        renderer.render_now(solid_skin(), P.PoseState())
        self.assertEqual(renderer.span_size, (renderer.width, 72))

    def test_a_held_item_adds_geometry_in_the_main_hand(self):
        renderer = model3d.ModelRenderer(height=80)
        empty = renderer.render_now(solid_skin((200, 120, 60)), P.PoseState())
        holding = renderer.render_now(solid_skin((200, 120, 60)),
                                      P.PoseState(held_main_rgb=0x40FF80))
        self.assertNotEqual(empty, holding, "a held item must change what is drawn")
        # The item is a green cuboid: its colour must appear, and only when something is held.
        def greenish(spans):
            return sum(1 for _x, _y, _run, c in spans
                       if ((c >> 8) & 0xFF) > ((c >> 16) & 0xFF))
        self.assertEqual(greenish(empty), 0)
        self.assertGreater(greenish(holding), 0)

    def test_hurt_time_tints_the_model_red(self):
        renderer = model3d.ModelRenderer(height=64)

        def mean_red(spans):
            total = sum(((c >> 16) & 0xFF) * run for _x, _y, run, c in spans)
            pixels = sum(run for _x, _y, run, _c in spans)
            return total / max(1, pixels)

        calm = renderer.render_now(solid_skin((80, 80, 200)), P.PoseState())
        hurt = renderer.render_now(solid_skin((80, 80, 200)), P.PoseState(hurt_time=1.0))
        self.assertGreater(mean_red(hurt), mean_red(calm))


class PoseKeyTests(unittest.TestCase):
    def test_tiny_changes_reuse_the_cached_render(self):
        a = P.PoseState(body_yaw=10.0, head_yaw=10.0, limb_swing=1.00)
        b = P.PoseState(body_yaw=10.4, head_yaw=10.2, limb_swing=1.01)
        self.assertEqual(model3d.pose_key(a, 1, 64), model3d.pose_key(b, 1, 64))

    def test_a_real_turn_invalidates_the_cache(self):
        a = P.PoseState(body_yaw=0.0)
        b = P.PoseState(body_yaw=45.0)
        self.assertNotEqual(model3d.pose_key(a, 1, 64), model3d.pose_key(b, 1, 64))

    def test_skin_and_size_are_part_of_the_key(self):
        pose = P.PoseState()
        self.assertNotEqual(model3d.pose_key(pose, 1, 64), model3d.pose_key(pose, 2, 64))
        self.assertNotEqual(model3d.pose_key(pose, 1, 64), model3d.pose_key(pose, 1, 96))

    def test_the_key_is_hashable_and_stable(self):
        pose = P.PoseState(body_yaw=33.0, limb_swing=2.5)
        key = model3d.pose_key(pose, 9, 72)
        self.assertEqual(key, model3d.pose_key(pose, 9, 72))
        self.assertIsInstance(hash(key), int)


class PerformanceTests(unittest.TestCase):
    def test_a_full_render_stays_inside_the_canvas_budget(self):
        # Borderlands 2 has to draw these as individual Canvas rectangles every frame, so the
        # span count - not the pixel count - is the real cost.
        renderer = model3d.ModelRenderer(height=96)
        spans = renderer.render_now(blocky_skin(), P.PoseState(body_yaw=135.0,
                                                               limb_swing=1.2,
                                                               limb_swing_amount=1.0))
        self.assertLess(len(spans), 900, f"{len(spans)} spans is too many to blit per frame")


if __name__ == "__main__":
    unittest.main(verbosity=2)
