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

> **Status: early scaffolding.** The shared-memory protocol is done and tested. The BL2 side is
> packaged as a menu-registered willow2-sdk mod (current `mods_base` API, with a legacy fallback),
> but the game adapters and Minecraft gameplay bridge are still scaffolding. Expect rough edges.
> This is a fan project. It isn't affiliated with Gearbox, 2K, Mojang or Microsoft, and you need
> to own both games.

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

# package the BL2 mod as BorderCraft.sdkmod + legacy folder ZIP
python3 tools/package_bl2.py

# Minecraft mod
cd fabric && ./gradlew build        # jar in build/libs/

# protocol change? edit protocol/bordercraft_protocol.h, then update BOTH mirrors:
#   protocol/python/bordercraft_protocol.py
#   fabric/src/main/java/dev/bordercraft/link/Proto.java
# and bump VERSION/kVersion everywhere.
```

## Roadmap (short version — details in docs/DESIGN.md)

1. ✅ **Phase 0** — shared-memory protocol + self-test (handshake, states, rings, teleport).
2. 🔶 **Phase 1a** — draw Minecraft's frames inside BL2's window. The overlay double buffer is
   protocol-tested; Minecraft now publishes after its in-world HUD render. GL readback still needs
   in-game verification, and the UE3 fullscreen blit is not implemented yet.
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
