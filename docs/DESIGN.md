# BorderCraft — Design Doc

> Play Borderlands 2 as the main game while *being* a Minecraft player: real Minecraft movement
> physics, inventory, items, block placing and combat, inside the real Pandora, able to fight
> bandits and wildlife. Modeled on [chasmlol/SkyCraft](https://github.com/chasmlol/SkyCraft)
> ("play Skyrim as a Minecraft player") — same bridge architecture, different host game.

Status: draft v0.1 · 2026-10-02

---

## 1. Core principle

**Neither game is rewritten.** Minecraft runs its own, unmodified game logic: movement, collision
resolution, combat math, inventory, crafting, block logic, rendering of items and hands. Borderlands 2
runs its own world: maps, bandits, wildlife, AI, missions, loot, saves.

The two mods only **translate** between them:

- BL2 tells Minecraft *what the world is shaped like* and *where the NPCs are*.
- Minecraft tells BL2 *where the player is*, *what the player hit*, and *what to draw on top*.

If we ever find ourselves re-implementing a Minecraft mechanic in Python or a BL2 mechanic in Java,
the design has gone wrong.

## 2. Target environment

| Thing | Value | Notes |
|---|---|---|
| Borderlands 2 | PC (Steam/Epic) | Modding is PC-only |
| Extender | **Python SDK (willow2-sdk)** | Installed into the game folder; mods live in `sdk_mods/` |
| Minecraft | Java Edition + **Fabric** | Yarn mappings; version pinned in `fabric/gradle.properties` |
| Java | 21+ | 25 works too (matches SkyCraft's toolchain) |
| Transport (v1) | file-backed shared mapping (`bridge.mm`) | Named mapping `Local\BorderCraft_v1` is the Phase-2 upgrade |
| Dev tools | Git, Python 3.9+, JDK | `tools/protocol_selftest.py` runs without either game |

Unlike SkyCraft (SKSE C++ plugin ↔ Fabric mod), the BL2 side is **Python inside the game** via the
Python SDK, which embeds CPython and gives us hooks into the engine each frame. The heavy data path
is still raw shared memory — Python only marshals bytes.

## 3. Components

```
┌────────────────── Borderlands2.exe ──────────────────┐      ┌──────────── javaw.exe (Minecraft) ─────────────┐
│  BorderCraft  (Python SDK mod)                       │      │  bordercraft (Fabric mod)                      │
│                                                      │      │                                                │
│  CollisionExporter ─ BL2 collision near player ──────┼─────▶│  CollisionField → injected into MC collision   │
│  ActorMirror      ─ nearby pawns (pos, box, hp) ─────┼─────▶│  ActorProxy entities (invisible, hittable)     │
│  InputBridge      ─ raw keyboard/mouse ──────────────┼─────▶│  input handlers (as if MC window had focus)    │
│  DamageApplier    ─ "NPC hit player for X" ──────────┼─────▶│  player.hurt(bordercraft:bl2_* damage source)  │
│                                                      │      │                                                │
│  PlayerPuppet    ◀─ player pos / look / pose ────────┼──────│  real MC LocalPlayer physics                   │
│  CameraDriver    ◀─ view (fov, bob, eye) ────────────┼──────│  GameRenderer camera                           │
│  HitBridge       ◀─ "you hit NPC 7 for 21.5" ────────┼──────│  ActorProxy.hurt() hook                        │
│  Compositor      ◀─ BGRA frames (overlay slots) ─────┼──────│  offscreen render: world / hand / GUI layers   │
└──────────────────────────────────────────────────────┘      └────────────────────────────────────────────────┘
                    shared memory (bridge.mm / Local\BorderCraft_v1)
                    protocol/bordercraft_protocol.h is the byte-layout source of truth
```

Plus one small shared piece: **`protocol/`** — the message schema used by both sides.

## 4. Coordinate mapping

BL2 (Unreal Engine 3) is Z-up, Minecraft is Y-up. Scale: **1 block = 53.3333 uu** (BL2 pawns are
~96 uu tall; the MC player is 1.8 blocks). Same trick as SkyCraft's 70-units-per-block.

```
mc.x =  ue.x / 53.3333
mc.y =  ue.z / 53.3333
mc.z =  ue.y / 53.3333          (exact signs pinned in Phase 0 with a calibration test)
mc.yaw   = f(ue.Yaw)            (UE rotators are 16384 = 90°; formula pinned in Phase 0)
mc.pitch = f(ue.Pitch)
```

- **Worlds:** each BL2 map (or fast-travel region) maps to its own MC dimension/region slot of the
  mirror world. A map change bumps `collisionEpoch` and `teleportSeq`.
- **Vertical range:** Pandora's terrain is well within MC's build height; one dimension is enough.

## 5. The mirror world (Minecraft side)

The MC mod runs a normal singleplayer world ("mirror world") with:

- **Void generator:** no MC blocks except the ones the player places.
- `doMobSpawning false`, `doWeatherCycle false`, `doDaylightCycle false`. Time and weather come
  from BL2 (`gameHour` analog: `Bl2State.gameSpeed` + map lighting).
- The integrated server runs normally; physics runs on the client as usual. Because collision
  injection happens in common code, the server's movement validation sees the same world.

### 5.1 CollisionField: how Pandora's shape reaches MC physics

MC movement (`Entity.collide` and friends) asks the level for collision shapes (`VoxelShape`s,
unions of AABBs) along the movement path. A Mixin appends **extra shapes from the CollisionField**
to that query. MC's own collision resolution, step-up, gravity, sprint-jumping, sneaking-at-edges
then run completely unchanged.

- Sparse store of AABBs / heightfield cells, bucketed per 16³ section, at **1/8-block resolution**.
- These shapes are **not blocks** — player blocks can sit next to BL2 geometry freely.
- **Slopes** become 1/8-block micro-steps (well under MC's 0.6 step height).
- **Steep surfaces** (> ~50°) are flagged `kColSteep` and raised into wall columns, a data decision,
  not a physics change.

**Where the data comes from (BL2 side, CollisionExporter):**

| Stage | Method | Covers | Cost |
|---|---|---|---|
| A: MVP | UE3 line traces on a grid around the player (≈48-block radius, amortized over frames), multi-hit per column | Terrain, most statics | Cheap; misses thin geometry |
| C: final | Walk `StaticMesh`/`Brush` collision of loaded components, voxelize on a worker thread | Everything, incl. opened doors | Complex but exact |

Data streams as deltas per section over the collision ring; MC evicts far sections.

**How it reaches Minecraft's physics (MC side).** Exactly one injection, and it is deliberately the
smallest one that can work: a `@ModifyVariable` on the static
`Entity.adjustMovementForCollisions(Entity, Vec3d, Box, World, List<VoxelShape>)` that appends
Pandora's shapes to the list Minecraft is about to solve against. Everything that makes Minecraft
movement feel like Minecraft — gravity, the 0.6 step-up, sprint jumping, sneaking at an edge,
slab-height precision — is then Mojang's unmodified code running against Pandora's geometry. The
mixin is listed under the common `mixins` key (not `client`) so the integrated server applies it
too, and it is declared `require = 0`: if a future mapping change stops it applying, the result is
"no Pandora collision" rather than a crash. That would otherwise be a silent failure, so
`CollisionField.mixinActive` records whether the handler has ever run and the bridge logs an
explicit error if collision data is arriving but the mixin never fires.

These shapes are deliberately **not** blocks. The mirror world stays a void world the player can
build in freely, and Pandora's geometry sits alongside the blocks they place rather than
overwriting them — which also keeps the block mirror's scan clean.

### 5.2 Water

BL2 water volumes around the player stream into `WaterGrid`; a Mixin reports `water` inside those
cells and MC's swimming physics applies unchanged.

## 6. The player

**Minecraft is authoritative for player position and physics.**

1. Each MC render frame the mod publishes `McState`: interpolated position/look (partial-tick render
   position), pose (standing, sneaking, swimming), the camera (fov, bob, eye height), and the raw
   20 Hz tick samples so BL2 can interpolate on its own frame clock without judder.
2. The BL2 side (**PlayerPuppet**) disables the pawn's own movement input and moves the
   `WillowPlayerPawn` (and its capsule) to that position every frame. The pawn stays in the world so
   NPC AI targeting, detection, triggers, mission checks and projectiles keep working.
3. **CameraDriver** keeps BL2's camera on the MC eye: yaw/pitch from `McState`, FOV converted
   (MC vertical ↔ UE3 horizontal), view bob applied. In v1 the MC overlay is composited over the
   BL2 frame, so the two views must match exactly.

**v1 shortcut (Phase 1a, now superseded):** keep BL2's own movement authoritative and slave MC's
player+camera to the BL2 pawn. **Shipped behaviour is the Phase 1b loop:** Minecraft is
authoritative, `GameAdapter.set_physics_authority` puts the BL2 pawn into `PHYS_Custom` with
`IgnoreMoveInput`/`IgnoreLookInput` set, and `move_puppet`/`camera_from` drive it from McState
every tick. **F6** hands authority back to Borderlands 2 at any time.

## 7. Rendering

The hard constraint: **no supported willow2-sdk API creates a runtime UE3 texture.** Everything
below follows from that one fact. Instead of uploading pixels, BorderCraft ships a software
rasterizer in Python (`bl2sdk/BorderCraft/raster.py`) and blits its output as run-length-coalesced
spans through `Canvas.SetDrawColor` + `DrawRect`. The budget is therefore *rectangles per frame*,
not pixels, and every renderer below is written against that budget.

- **The player (`model3d.py`, implemented).** The real Minecraft model: the same six boxes, the
  same 64×64 skin unwrap, both overlay layers, wide and slim arms. Model space is right-handed,
  16 units to a block, `+X` to the player's right, `+Y` up with the feet at `y=0`, and the model
  facing `−Z`. Minecraft's own model space is this rotated 180° about Z, so a vanilla
  `(pitch, yaw, roll)` applies as `rotation_matrix(-pitch, -yaw, roll)`, and because Minecraft's
  yaw grows clockwise from south the camera orbit is `180 − body_yaw`. Animation is vanilla's:
  `cos(limbSwing · 0.6662) · 1.4 · amount` legs, half that on the arms in antiphase, the ±75° head
  clamp, the sneak crouch, and `sin((1−(1−swing)³)·π)` for the hand swing. Rasterization is
  amortized over frames and cached against a quantized `pose_key()` (yaw to 6°, limb swing to ½,
  and so on), so a standing player costs nothing and a walking one re-renders a few faces a frame.
- **Placed blocks (`voxel.py`, implemented).** The block table is drawn as real 3D cubes: painter
  sorted far-to-near, back-face culled, faces buried between two blocks dropped, scanline-filled
  into horizontal Canvas bands, with Minecraft's own face shading (top 1.0, north/south 0.8,
  east/west 0.6, bottom 0.5) and a hard rectangle cap.
- **The HUD and inventory (`hud.py`, implemented).** Hearts, hunger, armour, absorption, air, the
  XP bar and level, the hotbar and the full 41-slot inventory. Items cannot be read out of
  Minecraft's atlas, so each slot crosses the bridge as a colour (a block's `MapColor`, or a
  rarity-biased hash of its registry id), a count, a damage fraction and a name; blocks are drawn
  as 16-rectangle isometric cubes and everything else as a tinted tile with text.
- **Full-frame composite (still blocked, and now optional).** Compositing Minecraft's *entire*
  rendered frame would still need a native D3D9 helper to upload a BGRA buffer. With the three
  renderers above that is a fidelity upgrade rather than a prerequisite.

## 8. Input

BL2 owns the window. The **InputBridge** (`bl2sdk/BorderCraft/inputmap.py`) hooks
`WillowPlayerController.InputKey`, translates UE3 key names into **GLFW codes** — Minecraft's own
namespace, so the Fabric side needs no second translation table — and pushes them through the input
ring. Mouse look is accumulated sub-pixel and forwarded as whole counts. Keys BL2 keeps:
**Esc** (menu), **Tab** (map), **F** (action skill), **~** (console), **F5/F6/F7** (BorderCraft's
own toggles), **Alt-F4**-class system keys.

`InputReplayer` on the Fabric side replays them into `KeyBinding.setKeyPressed` /
`KeyBinding.onKeyPressed` against an `InputUtil.Key`, which is exactly the state a focused window
would have set — so vanilla's own `handleInputEvents` performs the attack, the use, the hotbar
switch and continuous mining with no reimplementation. Mouse look becomes
`Entity#changeLookDirection`; the wheel becomes `PlayerInventory#scrollInHotbar`. When a Minecraft
screen is open, keys and clicks are routed to the `Screen` instead, against a virtual cursor driven
by the same mouse deltas. On BL2 menus (`kBl2MenuOpen`) or alt-tab (`kFocusLost`), every key the
bridge is holding is released — tracked per key, so nothing else is disturbed.

## 9. Combat

**The scaling rule, in both directions: damage crosses the bridge as a fraction of a 20-point
Minecraft health pool.** That is what keeps a diamond sword meaningful against a level 5 skag and
a level 50 badass without rewriting either game's combat maths.

- **MC → BL2.** Spawning a custom entity per pawn was rejected: registering an `EntityType` against
  a moving mapping surface is a liability for no gain. Instead `ActorMirror` keeps the pawn AABBs
  from the actor table and `DamageBridge` raycasts the crosshair against them, using Minecraft's
  own numbers — `GENERIC_ATTACK_DAMAGE`, the 1.9 cooldown ramp (`0.2 + p² · 0.8`) and the vanilla
  crit test — and emits `kEvtPlayerHitActor`. `GameAdapter.damage_actor` scales that into the
  pawn's health pool and calls `TakeDamage`, doubling on a crit.
- **BL2 → MC.** The MC→BL2 event ring is one-way, so v2 added a second ring at `kOffBl2EventRing`.
  `poll_player_damage` diffs the pawn's health each tick and pushes `kEvtBl2DamagePlayer` with a
  `kDamage*` kind; the Fabric side applies it to the *server* copy of the player with a real
  `DamageSource`, so armour, enchantments, absorption, the hurt tilt and the death screen are all
  vanilla behaviour.
- `kEvtApproachActor` implements BL2's "use" key near a pawn/terminal (talk, loot, press).

## 10. Progression

Like SkyCraft: BL2 XP, level and skills advance from MC play (melee trains a BL2 melee bonus,
ranged trains gun damage, etc. — mapping defined in `bl2sdk/BorderCraft/host.py`). BL2 saves remain
the single save system; MC's world saves placed blocks per map.

## 11. Protocol & transport

`protocol/bordercraft_protocol.h` is the byte-layout source of truth (little-endian), mirrored in
`protocol/python/bordercraft_protocol.py` and `fabric/.../link/Proto.java`. Regions: header,
`Bl2State`/`McState` (seqlocks), overlay double buffer, `WaterGrid`, input ring, collision ring,
actor table, event ring. Rings are SPSC with monotonic head/tail counters (the SkyCraft scheme).

**Transport v1:** a file-backed mapping at `%LOCALAPPDATA%\BorderCraft\bridge.mm`
(`$XDG_RUNTIME_DIR` on Linux). Both processes map the same file; writes are immediately visible
through the shared page cache. Phase 2 swaps in the named mapping `Local\BorderCraft_v1` (Windows
`CreateFileMapping`) — the struct layout is identical, only the open call changes.

## 12. Phases

| Phase | Deliverable | Status |
|---|---|---|
| **0** | Protocol + bridges handshake; calibration test for coords/rotators | ✅ protocol + `tools/protocol_selftest.py` (all channels, cross-process) |
| **1a** | Visible MC avatar; eventual overlay composite (BL2 movement) | ✅ Authenticated skin GPU readback, tagged overlay transport and UE3 Canvas drawing, all statically tested. Superseded by the 3D model below. |
| **1b** | MC physics authoritative; PlayerPuppet; input bridge | ✅ `PhysicsBridge` publishes McState with tick-interpolation endpoints; `GameAdapter.move_puppet`/`camera_from` slave the BL2 pawn and camera to it; `InputBridge` → `InputReplayer` replays UE3 key names as GLFW codes into Minecraft's `KeyBinding` state, including mouse look and GUI screens. |
| **2** | CollisionField (Stage A traces), ActorMirror, combat both ways, water | ✅ `CollisionExporter` traces heightfield sections and `EntityCollisionMixin` feeds them to Minecraft's solver; `ActorMirror` + `DamageBridge` run combat both ways. Water is flagged (`kColWater`) but buoyancy is not wired up. |
| **2.5** | Named-mapping transport, packaging, UX polish | 🔶 BL2 `.sdkmod` + legacy ZIP packaging is available; named mapping and UX polish remain |
| **3** | Native voxel rendering in BL2, block place/break carved into BL2 meshes (stretch) | 🔶 The player and placed blocks are rendered as real 3D geometry by the Python rasterizer. Carving into Pandora's own meshes, and Pandora lighting the blocks, remain stretch goals that need the native path. |

## 13. Known limitations (v1)

- Overlay compositing costs a CPU copy per frame (~8 MB at 1080p); fine on desktop GPUs, and the
  double buffer never blocks either game.
- BL2's UI (inventory, skills, map) is still BL2's; MC's inventory is the gameplay inventory
  (SkyCraft makes the same split).
- The overlay is a rectangle budget, not a framebuffer: roughly 250-520 rectangles for the avatar,
  up to 420 for blocks and ~900-1200 for the HUD. Item icons are flat colours and isometric cubes
  rather than Minecraft's own sprites, because reading the item atlas would need the native path.
- While a Minecraft screen is open, clicks are delivered to it at a virtual cursor; Minecraft's own
  hover highlight follows the real OS cursor, which cannot be moved for an unfocused window.
- `kColWater` is exported and the `WaterGrid` region exists, but buoyancy is not wired up yet: the
  collision field simply skips water columns instead of making them swimmable.
- The block mirror is a rolling scan of a 33x33 column region around the player, so a block placed
  far away (or by a command) can take up to about a second to appear in Pandora.
- Multiplayer: BL2 co-op clients without BorderCraft see a normal (puppeted) player. Full co-op
  sync is out of scope until single-player is solid.

## 14. Credits

Architecture and protocol design follow [chasmlol/SkyCraft](https://github.com/chasmlol/SkyCraft)
(see their `docs/DESIGN.md` and `protocol/skycraft_protocol.h`). Fan project; not affiliated with
Arkane, Bethesda, ZeniMax, Gearbox, 2K, Mojang or Microsoft. You need to own both games.
