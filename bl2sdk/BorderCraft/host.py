"""BorderCraft host-side logic for the Borderlands 2 Python SDK mod.

``GameAdapter`` is the only thing in BorderCraft that touches Unreal. Everything above it —
``BridgeRunner``, the renderers, the collision exporter — works on plain data, which is why the
whole BL2 side is testable without the game.

What this file is responsible for, end to end:

* **PlayerPuppet** — Minecraft owns the physics, so every frame the Borderlands 2 pawn is moved
  to the position Minecraft computed, with BL2's own movement input switched off. The pawn stays
  a real pawn, so bandit AI, aggro, triggers and mission logic keep working on it.
* **CameraDriver** — the BL2 camera is pinned to Minecraft's eye and FOV so the composited
  Minecraft visuals line up with Pandora.
* **CollisionExporter** — Pandora's shape, streamed to Minecraft's collision solver.
* **ActorMirror** — nearby Borderlands 2 pawns, published so Minecraft can hit them.
* **HitBridge** — Minecraft's hits become real Borderlands 2 damage, and Borderlands 2 damage
  becomes real Minecraft damage on the player.
* **InputBridge** — Borderlands 2 keystrokes replayed into Minecraft.
"""
from __future__ import annotations

import math
import threading
import zlib
from dataclasses import dataclass, field
from typing import Callable

try:
    from . import bordercraft_protocol as P
except ImportError:
    # Development checkout: the packaged sibling lives at protocol/python/.
    import bordercraft_protocol as P

from .collision import CollisionExporter, make_unreal_tracer
from .inputmap import InputBridge
from .render import Compositor, camera_forward
from .voxel import make_canvas_projector

# Minecraft hearts vs Borderlands 2 health: a Minecraft player has 20 half-hearts, a BL2
# character thousands of health points. Damage crosses the bridge as a fraction of max health so
# both games stay internally consistent at every character level.
MC_PLAYER_MAX_HEALTH = 20.0


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
        self._skin_world_view = False
        self._physics_authority = False
        self._actor_ids: dict[int, int] = {}
        self._actor_objects: dict[int, object] = {}
        self._next_actor_id = 1
        self._actor_health: dict[int, float] = {}
        self._tracer = None
        self._last_pawn_location = None

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

    @staticmethod
    def _make_vector(x: float, y: float, z: float):
        """Build a UE3 ``Vector`` struct on either SDK generation."""
        import unrealsdk  # type: ignore

        maker = getattr(unrealsdk, "make_struct", None) or getattr(unrealsdk, "MakeStruct")
        return maker("Vector", X=float(x), Y=float(y), Z=float(z))

    @staticmethod
    def _make_rotator(pitch: int, yaw: int, roll: int = 0):
        import unrealsdk  # type: ignore

        maker = getattr(unrealsdk, "make_struct", None) or getattr(unrealsdk, "MakeStruct")
        return maker("Rotator", Pitch=int(pitch), Yaw=int(yaw), Roll=int(roll))

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

    def read_mouse_delta(self) -> tuple[float, float]:
        """Raw mouse axes for this frame, straight off UE3's PlayerInput.

        Reading the axes (rather than hooking a mouse event) keeps the look bridge working with
        every input device Borderlands 2 supports, including controllers.
        """
        pc = self._pc()
        player_input = getattr(pc, "PlayerInput", None) if pc is not None else None
        if player_input is None:
            return (0.0, 0.0)
        try:
            return (float(player_input.aTurn), float(player_input.aLookUp))
        except (AttributeError, TypeError, ValueError):
            return (0.0, 0.0)

    def player_health(self) -> tuple[float, float]:
        pc = self._pc()
        pawn = getattr(pc, "Pawn", None) if pc is not None else None
        try:
            return float(pawn.Health), float(pawn.HealthMax)
        except (AttributeError, TypeError, ValueError):
            return (0.0, 0.0)

    # -- visible skin view --------------------------------------------------------------------
    @staticmethod
    def _set_pawn_mesh_hidden(pawn, hidden: bool) -> None:
        mesh = getattr(pawn, "Mesh", None) if pawn is not None else None
        if mesh is not None:
            try:
                mesh.SetHidden(hidden)
            except Exception:
                pass

    def set_skin_world_view(self, enabled: bool) -> bool:
        """Put BL2 in third person so the Minecraft model can stand where the pawn stands."""
        pc = self._pc()
        pawn = getattr(pc, "Pawn", None) if pc is not None else None
        if pc is None or pawn is None:
            self._skin_world_view = False
            return False
        try:
            pc.SetBehindView(bool(enabled))
            if enabled:
                # The same stable UE3 camera properties used by established BL2 third-person mods.
                pawn.CameraScale = 3.0
                pawn.CameraScaleRight = 1.5
                pawn.CameraScaleUp = 1.0
            # Leave the normal model visible until PostRender proves it can project the model.
            # This avoids an invisible pawn on legacy SDKs that cannot construct Vector structs.
            if not enabled:
                self._set_pawn_mesh_hidden(pawn, False)
            self._skin_world_view = bool(enabled)
        except Exception:
            self._skin_world_view = False
        return self._skin_world_view

    def toggle_skin_world_view(self) -> bool:
        return self.set_skin_world_view(not self._skin_world_view)

    def avatar_screen_bounds(self, canvas) -> tuple[float, float, float] | None:
        """Return (screen center X, feet Y, height) for the local pawn in third person."""
        if not self._skin_world_view or canvas is None:
            return None
        pc = self._pc()
        pawn = getattr(pc, "Pawn", None) if pc is not None else None
        location = getattr(pawn, "Location", None) if pawn is not None else None
        if location is None:
            return None
        try:
            half_height = float(pawn.CylinderComponent.CollisionHeight)
        except (AttributeError, TypeError, ValueError):
            half_height = 48.0
        try:
            feet_world = self._make_vector(location.X, location.Y, float(location.Z) - half_height)
            head_world = self._make_vector(location.X, location.Y, float(location.Z) + half_height)
            feet = canvas.Project(feet_world)
            head = canvas.Project(head_world)
            height = float(feet.Y) - float(head.Y)
            center_x = (float(feet.X) + float(head.X)) * 0.5
            clip_x, clip_y = float(canvas.ClipX), float(canvas.ClipY)
            if height < 48.0 or height > clip_y * 1.5:
                self._set_pawn_mesh_hidden(pawn, False)
                return None
            if center_x < -height or center_x > clip_x + height:
                self._set_pawn_mesh_hidden(pawn, False)
                return None
            self._set_pawn_mesh_hidden(pawn, True)
            return center_x, float(feet.Y), height
        except Exception:
            self._set_pawn_mesh_hidden(pawn, False)
            return None

    # -- PlayerPuppet: Minecraft is authoritative ---------------------------------------------
    def set_physics_authority(self, minecraft_authoritative: bool) -> bool:
        """Hand movement authority to Minecraft (or give it back to Borderlands 2).

        Borderlands 2 keeps simulating the pawn, it just stops steering it: ``IgnoreMoveInput``
        stops BL2's own movement code from fighting the puppet, while collision, AI perception
        and mission volumes continue to see a perfectly normal ``WillowPlayerPawn``.
        """
        pc = self._pc()
        if pc is None:
            return False
        want = bool(minecraft_authoritative)
        try:
            pc.IgnoreMoveInput(want)
        except Exception:
            return False
        try:
            # Look input goes to Minecraft too, otherwise the camera would be steered twice:
            # once by Borderlands 2 and once by the Minecraft player we are mirroring.
            pc.IgnoreLookInput(want)
        except Exception:
            pass
        self._physics_authority = want
        if not want:
            pawn = getattr(pc, "Pawn", None)
            try:
                if pawn is not None:
                    pawn.SetPhysics(1)  # PHYS_Walking: hand the pawn back to BL2's movement
            except Exception:
                pass
        return True

    def move_puppet(self, x: float, y: float, z: float, yaw: float, pitch: float) -> None:
        """Place the Borderlands 2 pawn where Minecraft's physics says the player is."""
        if not self._physics_authority:
            return
        pc = self._pc()
        pawn = getattr(pc, "Pawn", None) if pc is not None else None
        if pawn is None:
            return
        try:
            ue = P.mc_to_ue(x, y, z)
            try:
                half_height = float(pawn.CylinderComponent.CollisionHeight)
            except (AttributeError, TypeError, ValueError):
                half_height = 48.0
            # Minecraft reports feet; a UE3 pawn's origin is the capsule centre.
            location = self._make_vector(ue[0], ue[1], ue[2] + half_height)
            pawn.SetLocation(location)
            try:
                pawn.SetPhysics(10)  # PHYS_Custom: BL2 gravity must not fight Minecraft's
                pawn.Velocity = self._make_vector(0.0, 0.0, 0.0)
            except Exception:
                pass
            self._last_pawn_location = (x, y, z)
        except Exception:
            pass

    def camera_from(self, mc_state: "P.McState") -> None:
        """Pin the Borderlands 2 camera to Minecraft's eye, look direction and FOV."""
        if not self._physics_authority:
            return
        pc = self._pc()
        if pc is None:
            return
        try:
            pitch, yaw = P.mc_rotator_to_ue(mc_state.yaw, mc_state.pitch)
            pc.SetRotation(self._make_rotator(pitch, yaw))
        except Exception:
            pass
        try:
            # Minecraft's FOV is vertical, Unreal's is horizontal. 16:9 is the common case and
            # the viewport ratio refines it whenever Borderlands 2 reports a usable size.
            width, height = self.viewport_size()
            aspect = (width / height) if (width and height) else (16.0 / 9.0)
            vertical = math.radians(max(10.0, min(170.0, mc_state.fov_deg)))
            horizontal = 2.0 * math.atan(math.tan(vertical * 0.5) * aspect)
            pc.FOV(math.degrees(horizontal))
        except Exception:
            pass

    def apply_teleport(self, x: float, y: float, z: float) -> None:
        """Borderlands 2 moved the player (fast travel, mission, death): force the pawn there."""
        pc = self._pc()
        pawn = getattr(pc, "Pawn", None) if pc is not None else None
        if pawn is None:
            return
        try:
            ue = P.mc_to_ue(x, y, z)
            pawn.SetLocation(self._make_vector(ue[0], ue[1], ue[2] + 48.0))
        except Exception:
            pass

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

    def tracer(self):
        """A downward line-trace callable for the collision exporter (built once)."""
        if self._tracer is None:
            pc = self._pc()
            if pc is None:
                return None
            self._tracer = make_unreal_tracer(pc, self._make_vector)
        return self._tracer

    def water_grid(self) -> "P.WaterGrid | None":
        """Water volumes near the player, so Minecraft's swimming physics can apply."""
        return None  # BL2 water volumes are map-specific; the grid stays empty until mapped.

    # -- ActorMirror ---------------------------------------------------------------------------
    def _find_pawns(self):
        try:
            import unrealsdk  # type: ignore

            finder = getattr(unrealsdk, "find_all", None)
            if finder is not None:
                return list(finder("WillowAIPawn"))
            finder = getattr(unrealsdk, "FindAll", None)
            if finder is not None:
                return list(finder("WillowAIPawn"))
        except Exception:
            pass
        return []

    def _actor_id_for(self, pawn) -> int:
        key = id(pawn)
        handle = self._actor_ids.get(key)
        if handle is None:
            handle = self._next_actor_id
            self._next_actor_id = (self._next_actor_id + 1) & 0x7FFFFFFF or 1
            self._actor_ids[key] = handle
            self._actor_objects[handle] = pawn
        return handle

    def nearby_actors(self, radius_blocks: float = 48.0) -> list["P.ActorEntry"]:
        """Mirror the Borderlands 2 pawns around the player so Minecraft can fight them."""
        feet = self.player_feet_mc()
        limit = radius_blocks * radius_blocks
        out: list[P.ActorEntry] = []
        live: dict[int, object] = {}
        for pawn in self._find_pawns():
            try:
                location = pawn.Location
                mc = P.ue_to_mc(float(location.X), float(location.Y), float(location.Z))
            except Exception:
                continue
            dx, dy, dz = mc[0] - feet[0], mc[1] - feet[1], mc[2] - feet[2]
            if dx * dx + dy * dy + dz * dz > limit:
                continue
            try:
                health = float(getattr(pawn, "Health", 0.0) or 0.0)
                max_health = float(getattr(pawn, "HealthMax", 0.0) or 0.0) or max(health, 1.0)
            except (TypeError, ValueError):
                health, max_health = 0.0, 1.0
            try:
                half_w = float(pawn.CylinderComponent.CollisionRadius) / P.UNITS_PER_BLOCK
                height = float(pawn.CylinderComponent.CollisionHeight) * 2.0 / P.UNITS_PER_BLOCK
            except Exception:
                half_w, height = 0.4, 1.9
            try:
                yaw = P.ue_rotator_to_mc(0.0, float(pawn.Rotation.Yaw))[0]
            except Exception:
                yaw = 0.0
            flags = P.ACTOR_HOSTILE
            if health <= 0.0:
                flags |= P.ACTOR_DEAD
            if getattr(pawn, "IsChampion", None) and self._safe_bool(pawn.IsChampion):
                flags |= P.ACTOR_BOSS
            handle = self._actor_id_for(pawn)
            live[handle] = pawn
            self._actor_health[handle] = health
            out.append(P.ActorEntry(
                id=handle, flags=flags,
                x=mc[0], y=mc[1] - height * 0.5, z=mc[2], yaw=yaw,
                health=health, max_health=max_health,
                half_w=max(0.1, half_w), height=max(0.5, height),
            ))
        self._actor_objects = live or self._actor_objects
        return out

    @staticmethod
    def _safe_bool(value) -> bool:
        try:
            return bool(value() if callable(value) else value)
        except Exception:
            return False

    # -- combat --------------------------------------------------------------------------------
    def damage_actor(self, actor_id: int, damage: float, crit: bool,
                     hit_x: float, hit_y: float, hit_z: float) -> float:
        """A Minecraft hit on a mirrored pawn becomes real Borderlands 2 damage.

        Minecraft's damage is in half-hearts against a 20 point pool, so it is applied as the
        same *fraction* of the target's health pool. That keeps a diamond sword meaningful on a
        level 5 skag and on a level 50 badass without rewriting either game's combat maths.
        """
        pawn = self._actor_objects.get(actor_id)
        if pawn is None:
            return 0.0
        try:
            max_health = float(getattr(pawn, "HealthMax", 0.0) or 0.0)
        except (TypeError, ValueError):
            max_health = 0.0
        scaled = damage / MC_PLAYER_MAX_HEALTH * max(max_health, 1.0)
        if crit:
            scaled *= 2.0
        pc = self._pc()
        try:
            instigator = getattr(pc, "Pawn", None)
            # The hit location arrives in Minecraft coordinates; Unreal wants its own.
            ue_x, ue_y, ue_z = P.mc_to_ue(hit_x, hit_y, hit_z)
            pawn.TakeDamage(float(scaled), pc, self._make_vector(ue_x, ue_y, ue_z),
                            self._make_vector(0.0, 0.0, 0.0), None, None, instigator)
        except Exception:
            try:
                pawn.SetHealth(max(0.0, float(pawn.Health) - scaled))
            except Exception:
                return 0.0
        return scaled

    def poll_player_damage(self) -> list[tuple[float, int]]:
        """Detect Borderlands 2 damage on the pawn and express it in Minecraft half-hearts."""
        health, max_health = self.player_health()
        if max_health <= 0.0:
            self._last_health = None
            return []
        previous = getattr(self, "_last_health", None)
        self._last_health = health
        if previous is None or health >= previous:
            return []
        lost_fraction = (previous - health) / max_health
        hearts = lost_fraction * MC_PLAYER_MAX_HEALTH
        if hearts < 0.05:
            return []
        return [(hearts, P.DAMAGE_GUN)]

    def approach_actor(self, actor_id: int) -> None:
        """BL2 'use' near a pawn/terminal -> talk/loot/press."""
        pawn = self._actor_objects.get(actor_id)
        pc = self._pc()
        if pawn is None or pc is None:
            return
        try:
            pc.PerformedUseAction()
        except Exception:
            pass

    def progression_tick(self, mc_stats: dict) -> None:
        """Borderlands 2 XP advances from Minecraft play (melee trains melee, ranged ranged)."""
        experience = int(mc_stats.get("xp", 0))
        if experience <= 0:
            return
        pc = self._pc()
        try:
            pc.ExpLevelChange(experience, False)
        except Exception:
            try:
                pc.AddExperience(experience)
            except Exception:
                pass


@dataclass
class BridgeRunner:
    """The bridge loop. Engine-tick driven for reads; a light worker thread for heartbeats."""

    bridge: P.Bridge
    game: GameAdapter
    compositor: Compositor | None = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _epoch: int = 0
    _teleport_seq: int = 0
    _last_world_id: int | None = None
    _world_avatar: bool = False
    minecraft_physics: bool = False
    input_bridge: InputBridge | None = None
    collision: CollisionExporter | None = None
    frames: int = 0
    xp_pending: int = 0

    def __post_init__(self):
        self.compositor = Compositor(self.bridge)
        self.input_bridge = InputBridge(self.bridge.push_input)

    def stop(self) -> None:
        self._stop.set()

    # -- engine-thread entry points -----------------------------------------------------------
    def on_engine_tick(self) -> None:
        # Every Unreal UObject access stays on the engine thread. The worker below only keeps the
        # transport heartbeat alive while BL2 is loading and PlayerTick is not firing.
        self.bridge.heartbeat("bl2")
        self.frames += 1
        self.publish_bl2_state()
        if self.minecraft_physics and self.input_bridge is not None:
            dx, dy = self.game.read_mouse_delta()
            if dx or dy:
                # UE3's aLookUp is positive looking up; Minecraft's mouse dy is positive down.
                self.input_bridge.on_mouse_delta(dx, -dy)
        self.stream_collision()
        self.drain_events()
        self.forward_player_damage()
        if self.compositor:
            self.compositor.on_engine_frame(self.game.player_feet_mc())

    def on_post_render(self, canvas) -> None:
        """Draw Minecraft over the Borderlands 2 frame: blocks, the 3D player, then the HUD."""
        if not self.compositor:
            return
        bounds = None
        if self._world_avatar and self.compositor.skin is not None:
            bounds = self.game.avatar_screen_bounds(canvas)
        camera = project = None
        if self.compositor.blocks.blocks and canvas is not None:
            mc = self.bridge.read_mc_state()
            if mc is not None:
                camera = (mc.eye_x, mc.eye_y, mc.eye_z)
                project = make_canvas_projector(
                    canvas, self.game._make_vector,
                    camera_forward=camera_forward(mc.yaw, mc.pitch), camera_pos=camera,
                )
        self.compositor.on_post_render(canvas, bounds, camera, project)

    def on_input(self, params) -> None:
        """F5 toggles the world avatar; everything else is forwarded to Minecraft."""
        key = str(getattr(getattr(params, "Key", ""), "Name", getattr(params, "Key", ""))).upper()
        event = getattr(params, "Event", None)
        try:
            pressed = int(event) == 0  # UE3 IE_Pressed
        except (TypeError, ValueError):
            pressed = "PRESSED" in str(event).upper()

        if key == "F5" and pressed:
            self._world_avatar = self.game.toggle_skin_world_view()
            return
        if key == "F6" and pressed:
            self.set_minecraft_physics(not self.minecraft_physics)
            return
        if key == "F7" and pressed and self.compositor:
            self.compositor.show_hud = not self.compositor.show_hud
            return
        if self.input_bridge is not None:
            self.input_bridge.on_input_key(getattr(params, "Key", ""), event)

    def on_mouse_delta(self, dx: float, dy: float) -> None:
        if self.input_bridge is not None:
            self.input_bridge.on_mouse_delta(dx, dy)

    def set_minecraft_physics(self, enabled: bool) -> bool:
        """Flip movement authority between Borderlands 2 and Minecraft."""
        enabled = bool(enabled)
        if self.game.set_physics_authority(enabled):
            self.minecraft_physics = enabled
        if not enabled and self.input_bridge is not None:
            self.input_bridge.release_all()
        return self.minecraft_physics

    def on_mod_disable(self) -> None:
        """Restore the Borderlands 2 pawn, camera and input if the mod is switched off."""
        if self._world_avatar:
            self.game.set_skin_world_view(False)
            self._world_avatar = False
        if self.minecraft_physics:
            self.set_minecraft_physics(False)
        if self.input_bridge is not None:
            self.input_bridge.release_all()

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
            if self.collision is not None:
                self.collision.reset(self._epoch)

        in_game = self.game.in_game()
        menu_open = self.game.menu_open()
        loading = self.game.loading()
        paused = self.game.paused()
        viewport_w, viewport_h = self.game.viewport_size()
        if self.input_bridge is not None:
            self.input_bridge.set_suspended(menu_open or loading or not in_game)
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
        # The actor table is only worth refreshing a few times a second; pawns do not teleport.
        if self.frames % 3 == 0:
            actors = self.game.nearby_actors()
            if actors:
                self.bridge.write_actors(actors)

    def stream_collision(self) -> None:
        """Keep Minecraft's collision field fed with the shape of Pandora around the player."""
        if not self.minecraft_physics:
            return
        if self.collision is None:
            tracer = self.game.tracer()
            if tracer is None:
                return
            self.collision = CollisionExporter(tracer)
            self.collision.reset(self._epoch)
        for record in self.collision.step(self.game.player_feet_mc()):
            self.bridge.push_collision(record)

    def forward_player_damage(self) -> None:
        """Borderlands 2 hurt the pawn -> Minecraft hurts the player, with Minecraft's rules."""
        for hearts, kind in self.game.poll_player_damage():
            self.bridge.push_bl2_event(P.McEvent(
                type=P.EVT_BL2_DAMAGE_PLAYER, flags=kind, a=hearts,
            ))

    def drain_events(self) -> None:
        mc = self.bridge.read_mc_state()
        if mc is not None and self.minecraft_physics:
            self.game.move_puppet(mc.x, mc.y, mc.z, mc.yaw, mc.pitch)
            self.game.camera_from(mc)

        while True:
            ev = self.bridge.pop_event()
            if ev is None:
                break
            if ev.type == P.EVT_PLAYER_HIT_ACTOR:
                dealt = self.game.damage_actor(ev.actor_id, ev.a, bool(ev.b), ev.x, ev.y, ev.z)
                self.xp_pending += max(1, int(dealt * 0.1))
            elif ev.type == P.EVT_APPROACH_ACTOR:
                self.game.approach_actor(ev.actor_id)
            elif ev.type in (P.EVT_BLOCK_PLACE, P.EVT_BLOCK_BREAK):
                if self.compositor:
                    self.compositor.on_event(ev)
            elif ev.type == P.EVT_PLAYER_DIED:
                # Minecraft's death flow owns the moment; Borderlands 2 keeps its own save.
                if self.minecraft_physics:
                    self.set_minecraft_physics(False)
            elif ev.type == P.EVT_PLAYER_RESPAWNED:
                if not self.minecraft_physics:
                    self.set_minecraft_physics(True)

        if self.xp_pending and self.frames % 30 == 0:
            self.game.progression_tick({"xp": self.xp_pending})
            self.xp_pending = 0
