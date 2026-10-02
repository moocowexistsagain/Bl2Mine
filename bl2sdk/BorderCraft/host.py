"""BorderCraft host-side logic for the BL2 Python SDK mod.

GameAdapter isolates every call into Unreal so the bridge loop stays testable outside the game.
Each TODO marks a Phase-1/2 call that needs the real SDK surface (docs/DESIGN.md §6, §8, §9).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

try:
    from . import bordercraft_protocol as P
except ImportError:
    # Development checkout: the packaged sibling lives at protocol/python/.
    import bordercraft_protocol as P

from .render import OverlayCompositor


class GameAdapter:
    """All Unreal Engine touch points. Stubs are safe no-ops outside the game."""

    def __init__(self):
        self._teleport_seq = 0

    # -- player -------------------------------------------------------------------------------
    def player_feet_mc(self) -> tuple[float, float, float]:
        """Player pawn feet position in MC coords."""
        # TODO(Phase 1): read WillowPlayerPawn.Location and convert with P.ue_to_mc
        return (0.0, 64.0, 0.0)

    def player_look(self) -> tuple[float, float]:
        # TODO(Phase 1): from PlayerController rotation (UE rotator -> MC degrees)
        return (0.0, 0.0)

    def in_game(self) -> bool:
        # TODO(Phase 1): a save is loaded and the pawn exists
        return False

    def menu_open(self) -> bool:
        return False

    def move_puppet(self, x: float, y: float, z: float, yaw: float, pitch: float) -> None:
        """PlayerPuppet: set the pawn/capsule to the MC player's position each frame."""
        # TODO(Phase 1b): P.mc_to_ue + SetLocation/SetRotation; disable native movement input
        pass

    def camera_from(self, mc_state: P.McState) -> None:
        """CameraDriver: force BL2's camera onto the MC eye/fov/bob."""
        # TODO(Phase 1): fov is vertical in MC, horizontal in UE3 -> convert
        pass

    def apply_teleport(self, x: float, y: float, z: float) -> None:
        pass  # TODO(Phase 1b)

    # -- world ---------------------------------------------------------------------------------
    def world_id(self) -> int:
        """CRC32 of the current map name."""
        # TODO(Phase 1): crc32(WillowGame.GetCurrentMapName())
        return 0

    def sample_collision(self, epoch: int) -> list[bytes]:
        """CollisionExporter Stage A: grid of line traces around the player -> heightfield records."""
        # TODO(Phase 2): trace a 16x16 grid per section via PlayerController.Trace
        return []

    def water_grid(self) -> P.WaterGrid | None:
        # TODO(Phase 2): query WillowWaterVolumes near the player
        return None

    def nearby_actors(self) -> list[P.ActorEntry]:
        """ActorMirror: WillowAIPawn list within ~48 blocks."""
        # TODO(Phase 2): unreal.FindAllObjects("WillowGame.WillowAIPawn") filtered by distance
        return []

    # -- combat --------------------------------------------------------------------------------
    def damage_actor(self, actor_id: int, damage: float, crit: bool, hit_x: float, hit_y: float, hit_z: float) -> None:
        """HitBridge: MC hit a proxy -> real damage on the BL2 pawn (scaled to its level)."""
        # TODO(Phase 2): resolve handle -> pawn, pawn.TakeDamage(...)
        pass

    def damage_player(self, actor_id: int, damage: float) -> None:
        # TODO(Phase 2): mirror to MC as a synthetic bordercraft:bl2_* damage source (MC side)
        pass

    def approach_actor(self, actor_id: int) -> None:
        """BL2 'use' near a pawn/terminal -> talk/loot/press."""
        pass

    def progression_tick(self, mc_stats: dict) -> None:
        """BL2 XP/skills advance from MC play (melee/ranged mapping)."""
        pass


@dataclass
class BridgeRunner:
    """The bridge loop. Engine-tick driven for reads; a light worker thread for heartbeats."""

    bridge: P.Bridge
    game: GameAdapter
    compositor: OverlayCompositor | None = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _epoch: int = 0

    def __post_init__(self):
        self.compositor = OverlayCompositor(self.bridge)

    def stop(self) -> None:
        self._stop.set()

    # -- engine-thread entry points -----------------------------------------------------------
    def on_engine_tick(self) -> None:
        self.publish_bl2_state()
        self.drain_events()
        if self.compositor:
            self.compositor.on_engine_frame()  # pull Minecraft's latest frame (Phase 1a)

    def on_input(self, params) -> None:
        """Forward a raw input event to MC. BL2 keeps Esc/Tab/F/~."""
        # TODO(Phase 1b): translate UE input params -> P.InputEntry (skip BL2-owned keys)
        # self.bridge.push_input(P.InputEntry(...))
        pass

    # -- worker thread --------------------------------------------------------------------------
    def run(self) -> None:
        while not self._stop.is_set():
            self.bridge.heartbeat("bl2")
            self.publish_bl2_state()
            self.drain_events()
            time.sleep(0.05)

    # -- channels -------------------------------------------------------------------------------
    def publish_bl2_state(self) -> None:
        feet = self.game.player_feet_mc()
        yaw, pitch = self.game.player_look()
        self.bridge.write_bl2_state(P.Bl2State(
            flags=(P.BL2_IN_GAME if self.game.in_game() else 0)
                  | (P.BL2_MENU_OPEN if self.game.menu_open() else 0),
            world_id=self.game.world_id(),
            collision_epoch=self._epoch,
            pos_x=feet[0], pos_y=feet[1], pos_z=feet[2],
            yaw=yaw, pitch=pitch,
        ))
        actors = self.game.nearby_actors()
        if actors:
            self.bridge.write_actors(actors)
        for rec in self.game.sample_collision(self._epoch):
            self.bridge.push_collision(rec)

    def drain_events(self) -> None:
        mc = self.bridge.read_mc_state()
        if mc is not None:
            self.game.move_puppet(mc.x, mc.y, mc.z, mc.yaw, mc.pitch)
            self.game.camera_from(mc)

        while True:
            ev = self.bridge.pop_event()
            if ev is None:
                break
            if ev.type == P.EVT_PLAYER_HIT_ACTOR:
                self.game.damage_actor(ev.actor_id, ev.a, bool(ev.b), ev.x, ev.y, ev.z)
            elif ev.type == P.EVT_ACTOR_HIT_PLAYER:
                self.game.damage_player(ev.actor_id, ev.a)
            elif ev.type == P.EVT_APPROACH_ACTOR:
                self.game.approach_actor(ev.actor_id)
            elif ev.type == P.EVT_PLAYER_DIED:
                pass  # TODO(Phase 2): mirror BL2's death/respawn flow
            # EVT_BLOCK_PLACE/BREAK: BL2 renders placed blocks from the MC world mirror (Phase 3)
