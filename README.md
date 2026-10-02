# BorderCraft

**Play Borderlands 2 as a Minecraft player.** You move with Minecraft's physics, carry Minecraft's
inventory and HUD, and place and break blocks in Pandora. You fight bandits and wildlife with
Minecraft weapons — and they fight back.

Neither game is rewritten. Minecraft runs its own game logic, and Borderlands 2 runs its world, NPCs,
missions and saves. A Borderlands 2 Python SDK mod and a Minecraft Fabric mod talk to each other
through shared memory. Minecraft runs hidden in the background, and its frames are drawn inside
Borderlands 2's window.

> BorderCraft is a mashup in the spirit of [SkyCraft](https://github.com/chasmlol/SkyCraft)
> ("play Skyrim as a Minecraft player") — same bridge architecture, but the host game is
> Borderlands 2 and you *are* a Minecraft character inside it.

> **Status: the full crossover is in.** You walk around Pandora as a 3D Minecraft player model -
> your own skin, Minecraft's walk cycle, Minecraft's gravity and step-up and sprint-jumping -
> standing on Borderlands 2's terrain, which is streamed into Minecraft's own collision solver.
> Your hotbar, hearts, hunger, armour, XP and the whole 41-slot inventory are drawn over the BL2
> window. You place and break blocks and they appear in Pandora as real 3D cubes. You hit bandits
> with Minecraft weapons and they hit back, with both sides scaling damage so a diamond sword means
> the same thing to a level 5 skag and a level 50 badass. Expect rough edges. This is a fan project;
> it isn't affiliated with Gearbox, 2K, Mojang or Microsoft, and you need to own both games.

## How it works

```
┌───────────── Borderlands 2 (Python SDK mod) ─────────────┐        ┌──────── Minecraft (Fabric mod) ─────────┐
│  collision, NPC table, input, damage  ─── shared memory ──┼──────▶│  MC physics, inventory, blocks, combat  │
│  player puppet, camera, compositor    ◀──  bridge.mm  ───┼───────│  hidden window, offscreen frames        │
└───────────────────────────────────────────────────────────┘        └─────────────────────────────────────────┘
```

- `protocol/bordercraft_protocol.h` — the byte layout both sides agree on (seqlocked states, SPSC
  rings for input/collision/events, a frame overlay double buffer).
- `bl2sdk/BorderCraft/` — the BL2-side mod (Python SDK): the host/bridge loop plus a complete
  software renderer (`raster.py`, `model3d.py`, `voxel.py`, `hud.py`), the input map and the
  collision exporter. No native code, no custom UE3 textures.
- `fabric/` — the Minecraft-side mod (Fabric).
- `docs/DESIGN.md` — the full design: coordinate mapping, mirror world, collision field, combat
  bridge, and the phase roadmap.
- `tools/protocol_selftest.py` — runs both sides of the protocol as two real processes and checks
  every channel. **No games needed** — this is what CI (and you) can run today.

## Repo layout

```
bordercraft/
├── protocol/
│   ├── bordercraft_protocol.h        # single source of truth for the wire layout
│   └── python/bordercraft_protocol.py  # Python mirror (BL2 side + tools)
├── bl2sdk/BorderCraft/               # BL2 SDK mod source (current + legacy API)
│   ├── host.py                       # the bridge loop: state, puppet, camera, combat, XP
│   ├── render.py                     # the compositor that owns every Minecraft-side visual
│   ├── raster.py / model3d.py        # software rasterizer + the 3D Minecraft player model
│   ├── voxel.py / hud.py             # mirrored blocks as 3D cubes; HUD and inventory
│   └── inputmap.py / collision.py    # UE3 -> GLFW input; Pandora's collision as heightfields
├── fabric/                           # Fabric mod (gradle project)
│   └── src/main/java/dev/bordercraft/
│       ├── bridge/                   # physics, pose, HUD, blocks, input, actors, damage
│       └── mixin/                    # the single injection into Minecraft's collision solver
├── docs/DESIGN.md
└── tools/
    ├── protocol_selftest.py          # cross-process protocol test (runs anywhere)
    ├── jar_check.py                  # static checks on the prebuilt Minecraft jar (no JDK)
    └── package_bl2.py                # builds the installable BL2 SDK packages
```

## Requirements

**Borderlands 2** (PC): the latest [willow2-sdk](https://bl-sdk.github.io/willow2-mod-db/).
BorderCraft ships as `release/BorderCraft.sdkmod` for the current SDK, plus a legacy folder ZIP for
older SDK installs.

**Minecraft** (Java Edition): Fabric Loader + Fabric API. The version is pinned in
`fabric/gradle.properties` — bump it to whatever you play.

## Installing

➡️ **See [INSTALL.md](INSTALL.md)** — the full Windows guide. Short version:

1. **BL2**: install the current willow2-sdk, copy `release/BorderCraft.sdkmod` into the game's
   `sdk_mods/` folder, launch Borderlands 2, then enable **BorderCraft** in the MODS menu. For an
   older SDK that predates `.sdkmod`, extract `release/BorderCraft-0.1.0-legacy.zip` into
   `sdk_mods/` instead.
2. **Minecraft** (1.21.1 + Fabric): drop Fabric API and the prebuilt **`release/bordercraft-0.1.0.jar`**
   into `mods/`, open a world. The log shows `BorderCraft: bridge open at ...` when the games
   are connected.

A prebuilt jar for Minecraft 1.21.1 ships in `release/` so you don't need a JDK. To build
yourself: `cd fabric && ./gradlew build` (JDK 21).

## Developing

```bash
# protocol self-test (no games required)
python3 tools/protocol_selftest.py

# bridge lifecycle regression tests (includes Windows mapped-file / EINVAL failures)
python3 tools/test_bridge_lifecycle.py

# BL2-side tests (no game needed)
python3 tools/test_host_adapter.py        # the SDK adapter and the bridge runner
python3 tools/test_compositor.py          # the overlay compositor
python3 tools/test_model_renderer.py      # the 3D player model and its pose cache
python3 tools/test_voxel_field.py         # mirrored blocks drawn as cubes
python3 tools/test_hud_renderer.py        # HUD, hotbar and the 41-slot inventory
python3 tools/test_input_bridge.py        # UE3 key names -> Minecraft key bindings
python3 tools/test_collision_export.py    # Pandora's collision, packed for MC physics

# package the BL2 mod as BorderCraft.sdkmod + legacy folder ZIP
python3 tools/package_bl2.py

# Minecraft mod
cd fabric && ./gradlew build        # jar in build/libs/

# check a prebuilt jar before shipping/replacing it (catches the view-handle crash modes)
python3 tools/jar_check.py

# protocol change? edit protocol/bordercraft_protocol.h, then update BOTH mirrors:
#   protocol/python/bordercraft_protocol.py
#   fabric/src/main/java/dev/bordercraft/link/Proto.java
# and bump VERSION/kVersion everywhere.
```

## Roadmap (short version — details in docs/DESIGN.md)

1. ✅ **Phase 0** — shared-memory protocol + self-test (handshake, states, rings, teleport).
2. ✅ **Phase 1a** — the authenticated 64x64 skin crosses the overlay buffer and is drawn through
   UE3 Canvas, with no native code and no runtime UE3 texture.
3. ✅ **Phase 1b** — Minecraft physics is authoritative. Minecraft drives a BL2 puppet and camera;
   BL2's keyboard and mouse are replayed into Minecraft's key bindings (`InputReplayer`), with
   Esc/Tab/F/~ and the F5-F7 toggles kept for Borderlands 2.
4. ✅ **Phase 2** — Pandora's collision is traced into heightfield sections and injected into
   Minecraft's own solver through one `Entity` mixin; pawns are mirrored into an actor table and
   combat runs in both directions, scaled by fraction of max health.
5. ✅ **Phase 3 (software path)** — the player is a real 3D Minecraft model and placed blocks are
   real 3D cubes, both rasterized in Python and blitted as run-length-coalesced Canvas spans.
   **Still open:** a native D3D9 texture uploader would let the *whole* Minecraft frame be
   composited instead of this rectangle-budgeted renderer, and digging into Pandora's own meshes
   (rather than alongside them) remains a stretch goal.

## Controls

| Key | What it does |
|---|---|
| **WASD / Space / Shift / Ctrl** | Minecraft movement: its gravity, step-up, sprinting and sneaking |
| **Mouse** | Minecraft look, at your Minecraft sensitivity |
| **Left / right mouse** | Minecraft attack and use — on blocks, and on Pandora's pawns |
| **1-9, wheel, E, Q** | Hotbar, inventory, drop: the Minecraft bindings, as you have them mapped |
| **F5** | Toggle the projected third-person avatar |
| **F6** | Toggle Minecraft physics authority (hand the pawn back to Borderlands 2) |
| **F7** | Toggle the Minecraft HUD and inventory overlay |
| **Esc / Tab / F / ~** | Kept by Borderlands 2: menu / map / action skill / console |

Every other key is read from *your* Minecraft key bindings, so a remapped control works without
touching BorderCraft.

## Credits

- [chasmlol/SkyCraft](https://github.com/chasmlol/SkyCraft) — the architecture, protocol design and
  the inspiration for this whole idea. If you like BorderCraft, go star SkyCraft.
- The Borderlands 2 modding community — Python SDK (willow2-sdk), OpenBLCMM, and everyone who
  documented UE3 internals.

## License

To be decided by the author(s). Fan project — don't sell it, and it must stay free.
