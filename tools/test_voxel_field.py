#!/usr/bin/env python3
"""Unit tests for the mirrored Minecraft blocks drawn inside Pandora.

Blocks the player places in Minecraft have to appear in Borderlands 2 as real 3D cubes - drawn
with Canvas rectangles, painter-sorted, back-face culled and hidden where two blocks touch. The
rectangle budget is the thing that keeps this affordable every frame, so it is asserted too.
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bl2sdk.BorderCraft import voxel  # noqa: E402

P = voxel.P


def entry(x, y, z, rgb=(200, 160, 120), flags=P.BLOCK_FULL_CUBE):
    return P.BlockEntry(x, y, z, rgb[0], rgb[1], rgb[2], flags)


def pinhole(camera, focal=600.0, cx=800.0, cy=450.0):
    """A projector matching the signature BlockField.build_rects expects."""
    camx, camy, camz = camera

    def project(mx, my, mz):
        depth = camz - mz
        if depth <= 0.25:
            return None
        scale = focal / depth
        return (cx + (mx - camx) * scale, cy - (my - camy) * scale, depth)

    return project


class MirrorTests(unittest.TestCase):
    def test_update_replaces_the_set_and_reports_change(self):
        field = voxel.BlockField()
        self.assertTrue(field.update([entry(1, 2, 3)], world_id=5, revision=1))
        self.assertEqual(set(field.blocks), {(1, 2, 3)})
        # Same revision: nothing to do, and the caller is told so.
        self.assertFalse(field.update([entry(9, 9, 9)], world_id=5, revision=1))
        self.assertEqual(set(field.blocks), {(1, 2, 3)})
        self.assertTrue(field.update([entry(9, 9, 9)], world_id=5, revision=2))
        self.assertEqual(set(field.blocks), {(9, 9, 9)})

    def test_a_new_world_always_replaces_the_set(self):
        field = voxel.BlockField()
        field.update([entry(0, 0, 0)], world_id=1, revision=7)
        self.assertTrue(field.update([entry(4, 4, 4)], world_id=2, revision=7))
        self.assertEqual(set(field.blocks), {(4, 4, 4)})

    def test_place_and_break_events_apply_immediately(self):
        field = voxel.BlockField()
        field.apply_event(P.McEvent(type=P.EVT_BLOCK_PLACE, actor_id=0x3366CC,
                                    flags=P.BLOCK_FULL_CUBE, x=2, y=3, z=4))
        self.assertEqual(field.blocks[(2, 3, 4)], (0x33, 0x66, 0xCC, P.BLOCK_FULL_CUBE))
        field.apply_event(P.McEvent(type=P.EVT_BLOCK_BREAK, x=2, y=3, z=4))
        self.assertNotIn((2, 3, 4), field.blocks)

    def test_breaking_a_block_that_is_not_there_is_harmless(self):
        field = voxel.BlockField()
        field.apply_event(P.McEvent(type=P.EVT_BLOCK_BREAK, x=0, y=0, z=0))
        self.assertEqual(field.blocks, {})

    def test_unrelated_events_are_ignored(self):
        field = voxel.BlockField()
        field.apply_event(P.McEvent(type=P.EVT_PLAYER_HIT_ACTOR, x=1, y=1, z=1))
        self.assertEqual(field.blocks, {})


class VisibilityTests(unittest.TestCase):
    def test_distant_blocks_are_dropped(self):
        field = voxel.BlockField(max_distance=10.0)
        field.update([entry(0, 0, 0), entry(0, 0, 50)], revision=1)
        visible = field.visible((0.0, 0.0, 0.0))
        self.assertEqual([pos for _d2, pos, _c in visible], [(0, 0, 0)])

    def test_blocks_are_sorted_far_to_near(self):
        field = voxel.BlockField(max_distance=100.0)
        field.update([entry(0, 0, 2), entry(0, 0, 8), entry(0, 0, 5)], revision=1)
        order = [pos[2] for _d2, pos, _c in field.visible((0.0, 0.0, 0.0))]
        self.assertEqual(order, [8, 5, 2], "painter's algorithm needs far blocks first")

    def test_the_nearest_blocks_survive_the_cap(self):
        field = voxel.BlockField(max_distance=100.0, max_blocks=3)
        field.update([entry(0, 0, d) for d in range(1, 10)], revision=1)
        kept = sorted(pos[2] for _d2, pos, _c in field.visible((0.0, 0.0, 0.0)))
        self.assertEqual(kept, [1, 2, 3])


class RasterTests(unittest.TestCase):
    def test_a_single_block_draws_rectangles(self):
        field = voxel.BlockField()
        field.update([entry(0, 0, 0)], revision=1)
        camera = (0.5, 0.5, 6.0)
        rects = field.build_rects(camera, pinhole(camera))
        self.assertTrue(rects)
        self.assertEqual(field.blocks_drawn, 1)
        for x, y, w, h, colour in rects:
            self.assertGreater(w, 0)
            self.assertGreater(h, 0)
            self.assertEqual(len(colour), 4)

    def test_hidden_faces_between_touching_blocks_are_culled(self):
        camera = (4.0, 2.0, 8.0)
        wall = voxel.BlockField()
        wall.update([entry(0, 0, 0), entry(1, 0, 0)], revision=1)
        culled = len(wall.build_rects(camera, pinhole(camera)))
        # occluders=set() disables the "buried between two blocks" test, so the difference is
        # exactly the cost of the faces that are hidden where the two blocks meet.
        unculled = len(wall.build_rects(camera, pinhole(camera), occluders=set()))
        self.assertLess(culled, unculled)

    def test_back_faces_are_never_drawn(self):
        field = voxel.BlockField()
        field.update([entry(0, 0, 0)], revision=1)
        camera = (0.5, 0.5, 6.0)
        # Looking straight down the Z axis at one block: at most three faces can be visible.
        drawn = set()
        for axis in range(3):
            for sign in (-1, 1):
                if field._front_facing(axis, sign, (0, 0, 0), camera):
                    drawn.add((axis, sign))
        self.assertLessEqual(len(drawn), 3)
        self.assertIn((2, 1), drawn)      # the +Z face, towards the camera
        self.assertNotIn((2, -1), drawn)  # the -Z face, away from it

    def test_blocks_behind_the_camera_are_skipped(self):
        field = voxel.BlockField()
        field.update([entry(0, 0, 20)], revision=1)
        camera = (0.5, 0.5, 6.0)
        self.assertEqual(field.build_rects(camera, pinhole(camera)), [])

    def test_translucent_blocks_are_drawn_with_alpha(self):
        field = voxel.BlockField()
        field.update([entry(0, 0, 0, flags=P.BLOCK_TRANSLUCENT)], revision=1)
        camera = (0.5, 0.5, 6.0)
        rects = field.build_rects(camera, pinhole(camera))
        self.assertTrue(rects)
        self.assertTrue(all(colour[3] < 255 for _x, _y, _w, _h, colour in rects))

    def test_the_rectangle_budget_is_respected_by_a_big_build(self):
        field = voxel.BlockField(max_distance=40.0, max_blocks=200, max_rects=420)
        field.update([entry(x, y, z)
                      for x in range(-4, 5) for y in range(0, 4) for z in range(-4, 5)],
                     revision=1)
        camera = (0.5, 2.0, 25.0)
        rects = field.build_rects(camera, pinhole(camera))
        self.assertLessEqual(len(rects), 420)
        self.assertEqual(field.rects_drawn, len(rects))

    def test_face_shading_follows_minecraft(self):
        # Minecraft shades the top brightest, then north/south, then east/west, then the bottom.
        self.assertEqual(voxel.SHADE["top"], 1.0)
        self.assertGreater(voxel.SHADE["north"], voxel.SHADE["east"])
        self.assertGreater(voxel.SHADE["east"], voxel.SHADE["bottom"])
        self.assertEqual(voxel.SHADE["north"], voxel.SHADE["south"])
        self.assertEqual(voxel.SHADE["east"], voxel.SHADE["west"])


class ProjectorTests(unittest.TestCase):
    def test_canvas_projector_rejects_points_behind_the_camera(self):
        class FakeCanvas:
            ClipX, ClipY = 1600, 900

            def Project(self, vector):
                class Out:
                    X, Y, Z = 0.0, 0.0, -5.0
                return Out()

        # The camera sits at the origin looking towards +Z; the point is behind it.
        project = voxel.make_canvas_projector(
            FakeCanvas(), lambda x, y, z: (x, y, z),
            camera_forward=(0.0, 0.0, 1.0), camera_pos=(0.0, 0.0, 10.0))
        self.assertIsNone(project(1.0, 2.0, 3.0))
        self.assertIsNotNone(project(1.0, 2.0, 30.0))

    def test_canvas_projector_converts_to_unreal_units(self):
        seen = []

        class FakeCanvas:
            ClipX, ClipY = 1600, 900

            def Project(self, vector):
                seen.append(vector)

                class Out:
                    X, Y, Z = 100.0, 200.0, 12.0
                return Out()

        project = voxel.make_canvas_projector(FakeCanvas(), lambda x, y, z: (x, y, z))
        point = project(1.0, 2.0, 3.0)
        self.assertEqual(point[:2], (100.0, 200.0))
        self.assertGreater(point[2], 0.0)
        # One Minecraft block is UNITS_PER_BLOCK Unreal units, and Unreal is Z-up.
        ue = seen[0]
        self.assertAlmostEqual(ue[0], 1.0 * P.UNITS_PER_BLOCK, places=3)
        self.assertAlmostEqual(ue[1], 3.0 * P.UNITS_PER_BLOCK, places=3)
        self.assertAlmostEqual(ue[2], 2.0 * P.UNITS_PER_BLOCK, places=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
