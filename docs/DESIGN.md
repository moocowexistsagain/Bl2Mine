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

**v1 shortcut (Phase 1a):** keep BL2's own movement authoritative and slave MC's player+camera to
the BL2 pawn. That alone yields "Minecraft graphics moving through Pandora"; the authority flips to
MC in Phase 1b (the real SkyCraft-style loop).

## 7. Rendering

- **Phase 1a native-free slice (implemented):** MC reads the authenticated player's 64×64 GPU skin
  into BGRA and sends it through the shared-memory overlay double buffer. BL2 caches that payload
  and draws a wide/slim pixel-art paper doll with UE3 Canvas rectangles. It is always visible and
  its limbs animate from BL2 pawn movement; no arbitrary UE3 texture upload is needed.
- **Phase 1a full composite (blocked):** MC renders its world (and hand + HUD) offscreen each frame.
  A native D3D9 helper must upload the BGRA frame because no supported willow2-sdk API for creating
  an arbitrary runtime UE3 texture has been established.
- **Phase 3 — native draw (stretch):** BL2 renders MC geometry itself (debug-draw boxes per visible
  voxel face, or batched meshes) so blocks take Pandora's lighting. This is where SkyCraft's
  "Minecraft lights light up Skyrim" fidelity lives; it's the last milestone, not the first.

## 8. Input

BL2 owns the window. The **InputBridge** hooks raw key/mouse events and forwards most of them
through the input ring; the Fabric mod replays them as if the MC window had focus. Keys BL2 keeps:
**Esc** (menu), **Tab** (map), **F** (action skill), **~** (console), **Alt-F4**-class system keys.
On BL2 menus (`kBl2MenuOpen`) or alt-tab (`kFocusLost`), MC releases all held keys.

## 9. Combat

- MC weapons damage BL2 pawns through the ActorProxy: the Fabric mod hooks melee/projectile hits on
  proxy entities and emits `kEvtPlayerHitActor`; BL2's **HitBridge** applies real damage
  (scaled to the pawn's level, like SkyCraft does) via the SDK.
- BL2 pawns fight back: their attacks on the puppet emit `kEvtActorHitPlayer`, MC calls
  `player.hurt()` with a synthetic `bordercraft:bl2_gun`/`bl2_explosion` damage source. MC's
  armor, hearts and death/respawn flow are untouched.
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
| **1a** | Visible MC avatar; eventual overlay composite (BL2 movement) | 🔶 Authenticated skin GPU readback, tagged overlay transport, wide/slim UE3 Canvas paper doll, PostRender hooks and BL2-movement animation are implemented and statically tested. Two-game runtime verification remains. Full Minecraft-world compositing still needs a native UE3/D3D9 uploader. |
| **1b** | MC physics authoritative; PlayerPuppet; input bridge | scaffolded |
| **2** | CollisionField (Stage A traces), ActorMirror, combat both ways, water | protocol ready, exporters stubbed |
| **2.5** | Named-mapping transport, packaging, UX polish | 🔶 BL2 `.sdkmod` + legacy ZIP packaging is available; named mapping and UX polish remain |
| **3** | Native voxel rendering in BL2, block place/break carved into BL2 meshes (stretch) | future |

## 13. Known limitations (v1)

- Overlay compositing costs a CPU copy per frame (~8 MB at 1080p); fine on desktop GPUs, and the
  double buffer never blocks either game.
- BL2's UI (inventory, skills, map) is still BL2's; MC's inventory is the gameplay inventory
  (SkyCraft makes the same split).
- Multiplayer: BL2 co-op clients without BorderCraft see a normal (puppeted) player. Full co-op
  sync is out of scope until single-player is solid.

## 14. Credits

Architecture and protocol design follow [chasmlol/SkyCraft](https://github.com/chasmlol/SkyCraft)
(see their `docs/DESIGN.md` and `protocol/skycraft_protocol.h`). Fan project; not affiliated with
Arkane, Bethesda, ZeniMax, Gearbox, 2K, Mojang or Microsoft. You need to own both games.
