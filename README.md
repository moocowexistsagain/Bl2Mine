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

> **Status: first playable visual slice.** Run both games, open a Minecraft world, and your active
> 64x64 Minecraft skin appears as a crisp pixel-art avatar in Borderlands 2. You keep normal BL2
> movement controls; the avatar's limbs animate as your BL2 pawn moves. Minecraft-authoritative
> physics, blocks, inventory and combat remain later milestones. Expect rough edges. This is a fan
> project; it isn't affiliated with Gearbox, 2K, Mojang or Microsoft, and you need to own both games.

## How it works

```
┌───────────── Borderlands 2 (Python SDK mod) ─────────────┐        ┌──────── Minecraft (Fabric mod) ─────────┐
│  collision, NPC table, input, damage  ─── shared memory ──┼──────▶│  MC physics, inventory, blocks, combat  │
│  player puppet, camera, compositor    ◀──  bridge.mm  ───┼───────│  hidden window, offscreen frames        │
└───────────────────────────────────────────────────────────┘        └─────────────────────────────────────────┘
```

- `protocol/bordercraft_protocol.h` — the byte layout both sides agree on (seqlocked states, SPSC
  rings for input/collision/events, a frame overlay double buffer).
- `bl2sdk/BorderCraft/` — the BL2-side mod (Python SDK).
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
├── fabric/                           # Fabric mod (gradle project)
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

# BL2 adapter + skin renderer tests (no game needed)
python3 tools/test_host_adapter.py
python3 tools/test_skin_renderer.py

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
2. 🔶 **Phase 1a** — the working native-free slice publishes the authenticated 64x64 skin through
   the overlay buffer and renders it in BL2 through UE3 Canvas. The paper doll supports wide/slim
   arms and movement animation. Fullscreen Minecraft-world compositing still needs a native D3D9
   texture uploader; final PostRender/skin-readback behavior needs verification in the two games.
3. **Phase 1b** — Minecraft physics authoritative (SkyCraft-style puppet loop) + input bridge.
4. **Phase 2** — Pandora collision into MC physics, NPC proxies, combat both ways, water.
5. **Phase 3** — native voxel rendering and digging into Pandora's meshes (stretch).

## Controls (planned)

| Key | Does |
|---|---|
| **Esc** | BL2 menu |
| **Tab** | BL2 map |
| **F** | BL2 action skill |
| **~** | BL2 console |
| everything else | Minecraft (WASD, Space, Shift, E, 1-9, mouse, T, F5, ...) |

## Credits

- [chasmlol/SkyCraft](https://github.com/chasmlol/SkyCraft) — the architecture, protocol design and
  the inspiration for this whole idea. If you like BorderCraft, go star SkyCraft.
- The Borderlands 2 modding community — Python SDK (willow2-sdk), OpenBLCMM, and everyone who
  documented UE3 internals.

## License

To be decided by the author(s). Fan project — don't sell it, and it must stay free.
