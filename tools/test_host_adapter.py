#!/usr/bin/env python3
"""Tests for the BL2 game adapter and game-thread bridge publisher.

Run with: python3 tools/test_host_adapter.py
No game or willow2-sdk installation is required.
"""
from __future__ import annotations

import os
import sys
import threading
import unittest
import zlib
from types import SimpleNamespace
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bl2sdk.BorderCraft import host  # noqa: E402

P = host.P


class FakeWorld:
    def __init__(self, name: str = "Sanctuary_P", dilation: float = 0.75):
        self.name = name
        self.TimeDilation = dilation

    def GetStreamingPersistentMapName(self):
        return self.name


class FakeEngine:
    def __init__(self, world: FakeWorld):
        self.world = world
        self.GameViewport = SimpleNamespace(Viewport=SimpleNamespace(SizeX=1600, SizeY=900))

    def GetCurrentWorldInfo(self):
        return self.world


class FakePc:
    def __init__(self, paused: bool = True):
        self.Pawn = SimpleNamespace(Location=SimpleNamespace(X=106.6666, Y=-53.3333, Z=3200.0))
        self.Rotation = SimpleNamespace(Pitch=8192, Yaw=0)
        self._paused = paused

    def IsPaused(self):
        return self._paused


class FakeOverlay:
    def acquire(self):
        return None


class FakeBridge:
    def __init__(self):
        self.overlay = FakeOverlay()
        self.states = []
        self.heartbeats = []
        self.heartbeat_seen = threading.Event()

    def heartbeat(self, side):
        self.heartbeats.append(side)
        self.heartbeat_seen.set()

    def write_bl2_state(self, state):
        self.states.append(state)

    def write_actors(self, _actors):
        raise AssertionError("empty actor lists must not be published")

    def push_collision(self, _record):
        raise AssertionError("no collision was expected")

    def read_mc_state(self):
        return None

    def pop_event(self):
        return None


class FakeGame:
    def __init__(self):
        self.map_id = 0xCAFE
        self.is_paused = False
        self.skin_world_view = False

    def player_feet_mc(self):
        return (1.0, 2.0, 3.0)

    def player_look(self):
        return (-90.0, 12.5)

    def in_game(self):
        return True

    def menu_open(self):
        return self.is_paused

    def loading(self):
        return False

    def paused(self):
        return self.is_paused

    def viewport_size(self):
        return (1920, 1080)

    def game_speed(self):
        return 0.5

    def world_id(self):
        return self.map_id

    def nearby_actors(self):
        return []

    def sample_collision(self, _epoch):
        return []

    def toggle_skin_world_view(self):
        self.skin_world_view = not self.skin_world_view
        return self.skin_world_view

    def set_skin_world_view(self, enabled):
        self.skin_world_view = bool(enabled)
        return self.skin_world_view

    def avatar_screen_bounds(self, _canvas):
        return (400.0, 500.0, 256.0) if self.skin_world_view else None


class GameAdapterTests(unittest.TestCase):
    def test_reads_live_player_world_and_viewport(self):
        pc = FakePc()
        world = FakeWorld()
        adapter = host.GameAdapter(lambda: pc, lambda: FakeEngine(world))

        for actual, expected in zip(adapter.player_feet_mc(), (2.0, 60.0, -1.0)):
            self.assertAlmostEqual(actual, expected, places=4)
        self.assertEqual(adapter.player_look(), (-90.0, -45.0))
        self.assertTrue(adapter.in_game())
        self.assertTrue(adapter.paused())
        self.assertTrue(adapter.menu_open())
        self.assertFalse(adapter.loading())
        self.assertEqual(adapter.viewport_size(), (1600, 900))
        self.assertEqual(adapter.game_speed(), 0.75)
        self.assertEqual(
            adapter.world_id(),
            zlib.crc32(b"sanctuary_p") & 0xFFFFFFFF,
        )

    def test_loading_and_missing_sdk_states_are_safe(self):
        world = FakeWorld()
        loading = host.GameAdapter(lambda: SimpleNamespace(Pawn=None), lambda: FakeEngine(world))
        self.assertTrue(loading.loading())
        self.assertFalse(loading.in_game())
        self.assertEqual(loading.player_feet_mc(), (0.0, 64.0, 0.0))

        absent = host.GameAdapter(lambda: None, lambda: None)
        self.assertFalse(absent.in_game())
        self.assertFalse(absent.loading())
        self.assertFalse(absent.paused())
        self.assertEqual(absent.world_id(), 0)
        self.assertEqual(absent.viewport_size(), (0, 0))
        self.assertEqual(absent.game_speed(), 1.0)

    def test_third_person_skin_projects_over_pawn_and_restores_mesh(self):
        class Mesh:
            hidden = False

            def SetHidden(self, value):
                self.hidden = bool(value)

        mesh = Mesh()
        pawn = SimpleNamespace(
            Location=SimpleNamespace(X=10.0, Y=20.0, Z=100.0),
            CylinderComponent=SimpleNamespace(CollisionHeight=48.0),
            Mesh=mesh,
        )

        class Pc:
            Pawn = pawn
            bBehindView = False

            def SetBehindView(self, enabled):
                self.bBehindView = bool(enabled)

        pc = Pc()
        adapter = host.GameAdapter(lambda: pc, lambda: None)
        fake_sdk = SimpleNamespace(
            make_struct=lambda _name, **fields: SimpleNamespace(**fields),
        )

        class Canvas:
            ClipX, ClipY = 800, 600

            @staticmethod
            def Project(vector):
                return SimpleNamespace(X=400.0, Y=500.0 - (vector.Z - 52.0) * 2.0)

        with patch.dict(sys.modules, {"unrealsdk": fake_sdk}):
            self.assertTrue(adapter.set_skin_world_view(True))
            self.assertTrue(pc.bBehindView)
            self.assertEqual(adapter.avatar_screen_bounds(Canvas()), (400.0, 500.0, 192.0))
            self.assertTrue(mesh.hidden)
            self.assertFalse(adapter.set_skin_world_view(False))
            self.assertFalse(pc.bBehindView)
            self.assertFalse(mesh.hidden)

    def test_rotation_conversion_cardinal_directions_and_wrapping(self):
        self.assertEqual(P.ue_rotator_to_mc(0, 0), (-90.0, 0.0))       # UE +X -> MC +X
        self.assertEqual(P.ue_rotator_to_mc(0, 16384), (0.0, 0.0))     # UE +Y -> MC +Z
        self.assertEqual(P.ue_rotator_to_mc(16384, 32768), (90.0, -90.0))
        self.assertEqual(P.ue_rotator_to_mc(0, 65536), (-90.0, 0.0))
        for yaw, pitch in ((0.0, 0.0), (-90.0, -45.0), (135.0, 70.0)):
            ue_pitch, ue_yaw = P.mc_rotator_to_ue(yaw, pitch)
            actual_yaw, actual_pitch = P.ue_rotator_to_mc(ue_pitch, ue_yaw)
            self.assertAlmostEqual(actual_yaw, yaw, places=2)
            self.assertAlmostEqual(actual_pitch, pitch, places=2)


class BridgeRunnerTests(unittest.TestCase):
    def test_publishes_complete_state_and_bumps_map_epoch(self):
        bridge = FakeBridge()
        game = FakeGame()
        runner = host.BridgeRunner(bridge, game)

        runner.publish_bl2_state()
        first = bridge.states[-1]
        self.assertEqual(first.flags, P.BL2_IN_GAME)
        self.assertEqual(first.world_id, 0xCAFE)
        self.assertEqual(first.collision_epoch, 1)
        self.assertEqual(first.teleport_seq, 1)
        self.assertEqual((first.pos_x, first.pos_y, first.pos_z), (1.0, 2.0, 3.0))
        self.assertEqual((first.yaw, first.pitch), (-90.0, 12.5))
        self.assertEqual((first.viewport_w, first.viewport_h), (1920, 1080))
        self.assertEqual(first.game_speed, 0.5)

        runner.publish_bl2_state()
        self.assertEqual(bridge.states[-1].collision_epoch, 1)
        self.assertEqual(bridge.states[-1].teleport_seq, 1)

        game.map_id = 0xBEEF
        game.is_paused = True
        runner.publish_bl2_state()
        changed = bridge.states[-1]
        self.assertEqual(changed.collision_epoch, 2)
        self.assertEqual(changed.teleport_seq, 2)
        self.assertEqual(
            changed.flags,
            P.BL2_IN_GAME | P.BL2_MENU_OPEN | P.BL2_PAUSED,
        )

    def test_f5_toggles_world_skin_view_and_disable_restores_it(self):
        bridge = FakeBridge()
        game = FakeGame()
        runner = host.BridgeRunner(bridge, game)

        runner.on_input(SimpleNamespace(Key="F5", Event=0))
        self.assertTrue(runner._world_avatar)
        self.assertTrue(game.skin_world_view)
        runner.on_input(SimpleNamespace(Key="F5", Event=1))  # release does not toggle
        self.assertTrue(game.skin_world_view)

        runner.on_mod_disable()
        self.assertFalse(runner._world_avatar)
        self.assertFalse(game.skin_world_view)

    def test_worker_never_touches_unreal_adapter(self):
        class ForbiddenGame:
            def __getattribute__(self, name):
                raise AssertionError(f"worker touched game adapter: {name}")

        bridge = FakeBridge()
        runner = host.BridgeRunner(bridge, ForbiddenGame())
        thread = threading.Thread(target=runner.run)
        thread.start()
        self.assertTrue(bridge.heartbeat_seen.wait(0.5))
        runner.stop()
        thread.join(0.5)
        self.assertFalse(thread.is_alive())
        self.assertGreaterEqual(len(bridge.heartbeats), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
