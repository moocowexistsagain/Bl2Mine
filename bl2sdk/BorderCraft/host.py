"""BorderCraft host-side logic for the BL2 Python SDK mod.

GameAdapter isolates every call into Unreal so the bridge loop stays testable outside the game.
Each TODO marks a Phase-1/2 call that needs the real SDK surface (docs/DESIGN.md §6, §8, §9).
"""
from __future__ import annotations

import threading
import zlib
from dataclasses import dataclass, field
from typing import Callable

try:
    from . import bordercraft_protocol as P
except ImportError:
    # Development checkout: the packaged sibling lives at protocol/python/.
    import bordercraft_protocol as P

from .render import OverlayCompositor


class GameAdapter:
    """All Unreal Engine touch points, with safe fallbacks while no save is loaded.

    The optional getters keep this class testable without importing the game SDK. In-game they
    resolve to ``mods_base.get_pc``/``ENGINE`` on the current SDK and to ``unrealsdk.GetEngine``
    on legacy SDK installs.
    """

    def __init__(
        self,
        pc_getter: Callable[[], object | None] | None = None,
        engine_getter: Callable[[], object | None] | None = None,
    ):
        self._pc_getter = pc_getter
        self._engine_getter = engine_getter

    def _pc(self):
        try:
            if self._pc_getter is not None:
                return self._pc_getter()
            try:
                from mods_base import get_pc

                return get_pc()
            except ImportError:
                import unrealsdk  # type: ignore

                engine = unrealsdk.GetEngine()
                players = engine.GamePlayers
                return players[0].Actor if players else None
        except Exception:
            # Menus, loading screens, and shutdown can all briefly leave GamePlayers empty.
            return None

    def _engine(self):
        try:
            if self._engine_getter is not None:
                return self._engine_getter()
            try:
                from mods_base import ENGINE

                return ENGINE
            except ImportError:
                import unrealsdk  # type: ignore

                return unrealsdk.GetEngine()
        except Exception:
            return None

    def _world_info(self):
        engine = self._engine()
        if engine is None:
            return None
        try:
            return engine.GetCurrentWorldInfo()
        except Exception:
            return None

    # -- player -------------------------------------------------------------------------------
    def player_feet_mc(self) -> tuple[float, float, float]:
        """Player pawn origin in MC coordinates (the UE pawn origin is its feet reference)."""
        pc = self._pc()
        pawn = getattr(pc, "Pawn", None) if pc is not None else None
        location = getattr(pawn, "Location", None) if pawn is not None else None
        if location is None:
            return (0.0, 64.0, 0.0)
        try:
            return P.ue_to_mc(float(location.X), float(location.Y), float(location.Z))
        except (AttributeError, TypeError, ValueError):
            return (0.0, 64.0, 0.0)

    def player_look(self) -> tuple[float, float]:
        pc = self._pc()
        rotation = getattr(pc, "Rotation", None) if pc is not None else None
        if rotation is None:
            return (0.0, 0.0)
        try:
            return P.ue_rotator_to_mc(float(rotation.Pitch), float(rotation.Yaw))
        except (AttributeError, TypeError, ValueError):
            return (0.0, 0.0)

    def in_game(self) -> bool:
        pc = self._pc()
        return pc is not None and getattr(pc, "Pawn", None) is not None

    def paused(self) -> bool:
        pc = self._pc()
        if pc is None:
            return False
        try:
            return bool(pc.IsPaused())
        except Exception:
            return False

    def menu_open(self) -> bool:
        # BL2 pauses while its normal single-player menus own input. This conservative signal also
        # prevents stuck Minecraft keys for console/photo-mode pauses where no GFx menu is present.
        return self.paused()

    def loading(self) -> bool:
        # A world object without a possessed pawn is the stable condition during map transitions.
        return self._world_info() is not None and not self.in_game()

    def viewport_size(self) -> tuple[int, int]:
        """Best-effort viewport dimensions; unavailable SDK surfaces report zero."""
        engine = self._engine()
        try:
            viewport = engine.GameViewport.Viewport
            return int(viewport.SizeX), int(viewport.SizeY)
        except Exception:
            return (0, 0)

    def game_speed(self) -> float:
        world = self._world_info()
        try:
            return float(world.TimeDilation)
        except (AttributeError, TypeError, ValueError):
            return 1.0

    def move_puppet(self, x: float, y: float, z: float, yaw: float, pitch: float) -> None:
        """PlayerPuppet: set the pawn/capsule to the MC player's position each frame."""
        # TODO(Phase 1b): P.mc_to_ue + SetLocation/SetRotation; disable native movement input
        pass

    def camera_from(self, mc_state: P.McState) -> None:
        """CameraDriver: force BL2's camera onto the MC eye/fov/bob."""
        # TODO(Phase 1b): fov is vertical in MC, horizontal in UE3 -> convert
        pass

    def apply_teleport(self, x: float, y: float, z: float) -> None:
        pass  # TODO(Phase 1b)

    # -- world ---------------------------------------------------------------------------------
    def world_id(self) -> int:
        """Stable CRC32 of the lowercase persistent-map name."""
        world = self._world_info()
        if world is None:
            return 0
        try:
            map_name = str(world.GetStreamingPersistentMapName()).lower()
        except Exception:
            return 0
        return zlib.crc32(map_name.encode("utf-8")) & 0xFFFFFFFF

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
    _teleport_seq: int = 0
    _last_world_id: int | None = None

    def __post_init__(self):
        self.compositor = OverlayCompositor(self.bridge)

    def stop(self) -> None:
        self._stop.set()

    # -- engine-thread entry points -----------------------------------------------------------
    def on_engine_tick(self) -> None:
        # Every Unreal UObject access stays on the engine thread. The worker below only keeps the
        # transport heartbeat alive while BL2 is loading and PlayerTick is not firing.
        self.bridge.heartbeat("bl2")
        self.publish_bl2_state()
        self.drain_events()
        if self.compositor:
            # The Minecraft skin stays visible in BL2 and its limbs respond to the actual BL2
            # pawn movement. BL2 remains authoritative in this native-free Phase 1a slice.
            self.compositor.on_engine_frame(self.game.player_feet_mc())

    def on_post_render(self, canvas) -> None:
        """Draw the cached Minecraft skin through UE3's supported Canvas API."""
        if self.compositor:
            self.compositor.on_post_render(canvas)

    def on_input(self, params) -> None:
        """Forward a raw input event to MC. BL2 keeps Esc/Tab/F/~."""
        # TODO(Phase 1b): translate UE input params -> P.InputEntry (skip BL2-owned keys)
        # self.bridge.push_input(P.InputEntry(...))
        pass

    # -- worker thread --------------------------------------------------------------------------
    def run(self) -> None:
        while not self._stop.wait(0.05):
            # Unreal SDK objects are game-thread-only. Do not publish state or dispatch events
            # here; this worker exists solely so Minecraft can distinguish loading from a dead host.
            self.bridge.heartbeat("bl2")

    # -- channels -------------------------------------------------------------------------------
    def publish_bl2_state(self) -> None:
        feet = self.game.player_feet_mc()
        yaw, pitch = self.game.player_look()
        world_id = self.game.world_id()
        if world_id and world_id != self._last_world_id:
            self._epoch = (self._epoch + 1) & 0xFFFFFFFF
            self._teleport_seq = (self._teleport_seq + 1) & 0xFFFFFFFF
            self._last_world_id = world_id

        in_game = self.game.in_game()
        menu_open = self.game.menu_open()
        loading = self.game.loading()
        paused = self.game.paused()
        viewport_w, viewport_h = self.game.viewport_size()
        self.bridge.write_bl2_state(P.Bl2State(
            flags=(P.BL2_IN_GAME if in_game else 0)
                  | (P.BL2_MENU_OPEN if menu_open else 0)
                  | (P.BL2_LOADING if loading else 0)
                  | (P.BL2_PAUSED if paused else 0),
            world_id=world_id,
            collision_epoch=self._epoch,
            pos_x=feet[0], pos_y=feet[1], pos_z=feet[2],
            yaw=yaw, pitch=pitch,
            teleport_seq=self._teleport_seq,
            viewport_w=viewport_w,
            viewport_h=viewport_h,
            game_speed=self.game.game_speed(),
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
