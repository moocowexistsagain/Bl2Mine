# BorderCraft — Install Guide (Windows)

You own both games: **Borderlands 2 (PC)** and **Minecraft Java Edition**. The BL2 SDK package in
`release/` is ready to install, and the prebuilt Minecraft jar targets **Minecraft 1.21.1** (Fabric).
~10 minutes.

> **What this install gets you:** the "hello bridge" milestone — both mods load, the two real
> game processes connect over the shared memory, and you can watch them handshake. The
> gameplay composite (Minecraft drawn inside the Borderlands window) is Phase 1a and is not
> visible yet. Backup your saves anyway.

---

## Part 1 — Borderlands 2 side

1. **Install the Python SDK** (the modding framework, aka "willow2-sdk" / old name "PythonSDK"):
   - Open <https://bl-sdk.github.io/willow2-mod-db/> and follow **Installation Instructions**
     at the top: download the latest **`willow2-sdk.zip`** from the GitHub releases page it
     links. (Also install the Microsoft Visual C++ Redistributable if it asks.)
   - Extract the zip **directly into the Borderlands 2 game folder** so it merges —
     Steam default: `C:\Program Files (x86)\Steam\steamapps\common\Borderlands 2`
     (Epic: `C:\Program Files\Epic Games\Borderlands2`). You should end up with a
     **`sdk_mods\`** folder in the game directory. Accept overwrites if asked.

2. **Install BorderCraft**:
   - For willow2-sdk 3.x and newer, copy `release\BorderCraft.sdkmod` directly into
     `<game folder>\sdk_mods\`. Do not unzip the `.sdkmod` file.
   - For older PythonSDK installs, extract `release\BorderCraft-0.1.0-legacy.zip` into
     `<game folder>\sdk_mods\`. It contains one `BorderCraft` folder; the result must be
     `sdk_mods\BorderCraft\__init__.py`.
   - The package includes the shared protocol module; no second file copy is needed.

3. **Restart Borderlands 2.** Open **MODS** → find **BorderCraft** → enable it. The mod menu
   should show its BorderCraft name and description. When enabled, a bridge file appears at
   `%LOCALAPPDATA%\BorderCraft\bridge.mm` (the shared memory). To double-check, look for a
   `[BorderCraft] bridge up ...` line in
   `Documents\My Games\Borderlands 2\WillowGame\Logs\Launch.log`.

## Part 2 — Minecraft side

1. **Install Fabric Loader** for **Minecraft 1.21.1**: run the installer from
   <https://fabricmc.net/use/installer/>, pick game version 1.21.1, Install.

2. **Get Fabric API** for 1.21.1 from <https://modrinth.com/mod/fabric-api> (use the version
   dropdown) and put the downloaded `.jar` in your mods folder:
   `%appdata%\.minecraft\mods`

3. **Install BorderCraft**: copy `release\bordercraft-0.1.0.jar` (built for you in this repo)
   into the same `mods` folder.

4. **Launch Minecraft** (the "Fabric Loader" profile) and open any world. In
   `%appdata%\.minecraft\logs\latest.log` look for:
   ```
   BorderCraft: bridge open at C:\Users\<you>\AppData\Local\BorderCraft\bridge.mm
   ```
   That line means **the two games are connected**. BL2 and Minecraft can start in either
   order — the mod retries until the other side shows up.

## Expected result

- BL2: MODS menu shows BorderCraft, enabled, no errors; `bridge.mm` in `%LOCALAPPDATA%\BorderCraft\`.
- Minecraft: `BorderCraft: bridge open at ...` in the log.
- In-game: nothing visual yet (the frame composite is the next milestone) — this install proves
  the foundation.

## Troubleshooting

| Symptom | Fix |
|---|---|
| No MODS menu in BL2 | Python SDK didn't install into the game folder — the zip must merge so `sdk_mods\` sits next to `Binaries\`. Re-extract. |
| MODS menu has no BorderCraft | Check that `BorderCraft.sdkmod` is directly in `sdk_mods\` (do not unzip it). For the legacy ZIP, extract its single `BorderCraft` folder directly into `sdk_mods\`; avoid double nesting. Restart the game after installing. |
| BL2 log reports a Python import error | Reinstall the complete `.sdkmod` or legacy ZIP from `release/`; both packages include the protocol module. |
| MC log says `bad magic` / `protocol version` | Stale bridge from an older build: close both games, delete `%LOCALAPPDATA%\BorderCraft\bridge.mm`, start again. |
| MC log shows nothing about BorderCraft | The jar isn't in `mods\`, or you launched a non-Fabric profile. |
| MC crashes on startup | Check `latest.log` — if it's a mod conflict, try with only Fabric API + BorderCraft in `mods\`. |
| Wrong Minecraft version | The jar is for 1.21.1. For another version: edit `fabric/gradle.properties`, run `gradlew build` (needs JDK 21), replace the jar. |

## Uninstall

Delete `sdk_mods\BorderCraft.sdkmod` (or the legacy `sdk_mods\BorderCraft` folder), delete
`mods\bordercraft-0.1.0.jar`, and remove `%LOCALAPPDATA%\BorderCraft`. Nothing touches your saves.
