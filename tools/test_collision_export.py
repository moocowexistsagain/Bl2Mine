#!/usr/bin/env python3
"""Unit tests for streaming Pandora's collision into Minecraft's physics.

This is what makes "Minecraft physics in Borderlands 2" literally true: Minecraft's own
collision solver runs against heightfield sections traced out of the Borderlands 2 world. The
tests cover the record format the Fabric side decodes, the walkable/steep/water classification,
and the scheduling that keeps a whole-neighbourhood trace from stalling the game thread.
"""
from __future__ import annotations

import os
import struct
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bl2sdk.BorderCraft import collision  # noqa: E402

P = collision.P


def flat_tracer(height=64.0, normal_y=1.0, water=False, hits=True):
    calls = []

    def trace(x, z, top, bottom):
        calls.append((x, z, top, bottom))
        return (height, normal_y, water) if hits else None

    trace.calls = calls
    return trace


class PackingTests(unittest.TestCase):
    def test_pack_round_trips(self):
        columns = [((i * 3) % 500 - 100, (i % 4)) for i in range(256)]
        payload = collision.pack_heightfield(columns)
        self.assertEqual(len(payload), 256 * 4)
        self.assertEqual(collision.unpack_heightfield(payload), columns)

    def test_the_wrong_column_count_is_rejected(self):
        with self.assertRaises(ValueError):
            collision.pack_heightfield([(0, 0)] * 10)

    def test_heights_are_clamped_into_the_sixteen_bit_field(self):
        payload = collision.pack_heightfield([(999999, 0)] + [(0, 0)] * 255)
        self.assertEqual(collision.unpack_heightfield(payload)[0][0], 32767)

    def test_the_sentinel_survives_a_round_trip(self):
        payload = collision.pack_heightfield([(collision.NO_HEIGHT, 0)] * 256)
        self.assertTrue(all(h == collision.NO_HEIGHT for h, _f in
                            collision.unpack_heightfield(payload)))


class SurfaceTests(unittest.TestCase):
    def test_flat_ground_is_walkable(self):
        self.assertTrue(collision.surface_flags(1.0) & P.COL_WALKABLE)
        self.assertFalse(collision.surface_flags(1.0) & P.COL_STEEP)

    def test_a_cliff_is_steep(self):
        self.assertTrue(collision.surface_flags(0.1) & P.COL_STEEP)
        self.assertFalse(collision.surface_flags(0.1) & P.COL_WALKABLE)

    def test_the_slope_limit_sits_at_fifty_degrees(self):
        import math
        just_walkable = math.cos(math.radians(49.0))
        just_steep = math.cos(math.radians(51.0))
        self.assertTrue(collision.surface_flags(just_walkable) & P.COL_WALKABLE)
        self.assertTrue(collision.surface_flags(just_steep) & P.COL_STEEP)

    def test_water_is_flagged_separately(self):
        self.assertTrue(collision.surface_flags(1.0, water=True) & P.COL_WATER)

    def test_an_unknown_normal_is_assumed_walkable(self):
        self.assertTrue(collision.surface_flags(None) & P.COL_WALKABLE)


class RingTests(unittest.TestCase):
    def test_sections_come_out_nearest_first(self):
        ring = collision.section_ring((0, 4, 0), 2)
        self.assertEqual(ring[0], (0, 4, 0))
        self.assertEqual(len(ring), 25)
        self.assertEqual(len({s for s in ring}), 25)

    def test_the_section_y_is_carried_through(self):
        for section in collision.section_ring((3, 7, -2), 1):
            self.assertEqual(section[1], 7)


class ExporterTests(unittest.TestCase):
    def test_a_record_has_the_protocol_layout_the_fabric_side_decodes(self):
        exporter = collision.CollisionExporter(flat_tracer(height=70.5), radius=0)
        exporter.reset(epoch=9)
        records = exporter.step((8.0, 64.0, 8.0), budget=256)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(len(record), P.COLLISION_REC_BYTES)
        _seq, epoch, sx, sy, sz, kind, _count, _pad, _flags = struct.unpack_from(
            "<IIhhhBBHI", record, 0)
        self.assertEqual(epoch, 9)
        self.assertEqual((sx, sy, sz), (0, 4, 0))
        self.assertEqual(kind, P.COL_HEIGHTFIELD)
        heights = collision.unpack_heightfield(record[0x40:0x40 + 1024])
        # 70.5 blocks at 1/8-block resolution.
        self.assertTrue(all(h == int(round(70.5 * 8)) for h, _f in heights))
        self.assertTrue(all(f & P.COL_WALKABLE for _h, f in heights))

    def test_empty_space_is_recorded_as_the_sentinel(self):
        exporter = collision.CollisionExporter(flat_tracer(hits=False), radius=0)
        exporter.reset(epoch=1)
        record = exporter.step((0.0, 64.0, 0.0), budget=256)[0]
        heights = collision.unpack_heightfield(record[0x40:0x40 + 1024])
        self.assertTrue(all(h == collision.NO_HEIGHT for h, _f in heights))

    def test_tracing_is_amortized_over_frames(self):
        tracer = flat_tracer()
        exporter = collision.CollisionExporter(tracer, radius=0, columns_per_tick=32)
        exporter.reset(epoch=1)
        position = (0.0, 64.0, 0.0)
        frames = 0
        records = []
        while not records and frames < 100:
            records.extend(exporter.step(position))
            frames += 1
        self.assertEqual(frames, 8, "256 columns at 32 per tick is eight frames")
        self.assertEqual(len(records), 1)
        self.assertEqual(len(tracer.calls), 256)

    def test_a_published_section_is_not_traced_again(self):
        tracer = flat_tracer()
        exporter = collision.CollisionExporter(tracer, radius=0)
        exporter.reset(epoch=1)
        exporter.step((0.0, 64.0, 0.0), budget=256)
        before = len(tracer.calls)
        self.assertEqual(exporter.step((0.0, 64.0, 0.0), budget=256), [])
        self.assertEqual(len(tracer.calls), before)

    def test_walking_far_enough_schedules_the_new_neighbourhood(self):
        tracer = flat_tracer()
        exporter = collision.CollisionExporter(tracer, radius=0, refresh_distance=6.0)
        exporter.reset(epoch=1)
        exporter.step((0.0, 64.0, 0.0), budget=256)
        before = len(tracer.calls)
        records = exporter.step((100.0, 64.0, 100.0), budget=256)
        self.assertEqual(len(records), 1)
        self.assertGreater(len(tracer.calls), before)

    def test_a_new_map_invalidates_everything(self):
        tracer = flat_tracer()
        exporter = collision.CollisionExporter(tracer, radius=0)
        exporter.reset(epoch=1)
        exporter.step((0.0, 64.0, 0.0), budget=256)
        exporter.reset(epoch=2)
        records = exporter.step((0.0, 64.0, 0.0), budget=256)
        self.assertEqual(len(records), 1)
        _seq, epoch = struct.unpack_from("<II", records[0], 0)
        self.assertEqual(epoch, 2)

    def test_columns_are_traced_at_block_centres_over_the_whole_section(self):
        tracer = flat_tracer()
        exporter = collision.CollisionExporter(tracer, radius=0)
        exporter.reset(epoch=1)
        exporter.step((0.0, 64.0, 0.0), budget=256)
        xs = sorted({c[0] for c in tracer.calls})
        zs = sorted({c[1] for c in tracer.calls})
        self.assertEqual(xs, [i + 0.5 for i in range(16)])
        self.assertEqual(zs, [i + 0.5 for i in range(16)])

    def test_the_trace_spans_above_and_below_the_player(self):
        tracer = flat_tracer()
        exporter = collision.CollisionExporter(tracer, radius=0)
        exporter.reset(epoch=1)
        exporter.step((0.0, 100.0, 0.0), budget=1)
        _x, _z, top, bottom = tracer.calls[0]
        self.assertGreater(top, 100.0)
        self.assertLess(bottom, 100.0)

    def test_the_record_counters_track_work_done(self):
        exporter = collision.CollisionExporter(flat_tracer(), radius=0)
        exporter.reset(epoch=1)
        exporter.step((0.0, 64.0, 0.0), budget=256)
        self.assertEqual(exporter.records_built, 1)
        self.assertEqual(exporter.columns_traced, 256)


class UnrealTracerTests(unittest.TestCase):
    class Vec:
        def __init__(self, x, y, z):
            self.X, self.Y, self.Z = x, y, z

    def test_a_hit_is_converted_back_into_minecraft_coordinates(self):
        calls = []

        class FakePc:
            def Trace(self, end, start, complex_trace):
                calls.append((start, end))
                return (object(),
                        UnrealTracerTests.Vec(100.0, 200.0, 64.0 * P.UNITS_PER_BLOCK),
                        UnrealTracerTests.Vec(0.0, 0.0, 1.0))

        trace = collision.make_unreal_tracer(FakePc(), lambda x, y, z: (x, y, z))
        hit_y, normal_y, water = trace(1.0, 2.0, 100.0, 20.0)
        self.assertAlmostEqual(hit_y, 64.0, places=3)
        self.assertAlmostEqual(normal_y, 1.0, places=6)
        self.assertFalse(water)
        # The trace runs downwards: the start is above the end.
        start, end = calls[0]
        self.assertGreater(start[2], end[2])

    def test_a_miss_is_reported_as_empty(self):
        class FakePc:
            def Trace(self, end, start, complex_trace):
                return None

        trace = collision.make_unreal_tracer(FakePc(), lambda x, y, z: (x, y, z))
        self.assertIsNone(trace(0.0, 0.0, 10.0, 0.0))

    def test_an_sdk_failure_never_escapes(self):
        class FakePc:
            def Trace(self, end, start, complex_trace):
                raise RuntimeError("the engine moved under us")

        trace = collision.make_unreal_tracer(FakePc(), lambda x, y, z: (x, y, z))
        self.assertIsNone(trace(0.0, 0.0, 10.0, 0.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
