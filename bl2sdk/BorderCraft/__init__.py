"""BorderCraft — Borderlands 2 Python SDK mod (the bridge host).

Drop this folder into ``Borderlands 2/sdk_mods/BorderCraft/``. BL2 creates the shared mapping;
the Minecraft Fabric mod opens it (see docs/DESIGN.md).

This is a Phase-0/1 skeleton: the bridge, heartbeat, state publishing and event draining all
work against the protocol; the calls into Unreal (puppet move, traces, damage) are stubbed in
host.py and marked TODO. Imports of unrealsdk are guarded so the module can be imported (and its
protocol logic tested) outside the game.
"""
from __future__ import annotations

import os
import sys
import threading
import time

# Make the in-repo protocol mirror importable whether we're in sdk_mods/ or in the repo.
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (
    os.path.join(_HERE, "..", "..", "protocol", "python"),   # repo layout
    os.path.join(_HERE, "protocol"),                          # packaged layout
):
    if os.path.isdir(_p):
        sys.path.insert(0, _p)

import bordercraft_protocol as P  # noqa: E402

try:
    import unrealsdk  # type: ignore
    from Mods import ModMenu  # type: ignore
    IN_GAME = True
except ImportError:
    unrealsdk = None
    ModMenu = None
    IN_GAME = False

from . import host  # noqa: E402

if IN_GAME:

    class BorderCraft(ModMenu.SDKMod):
        Name = "BorderCraft"
        Author = "BorderCraft contributors"
        Description = "Play Borderlands 2 as a Minecraft player. Bridges BL2 to a hidden Minecraft."
        Version = "0.1.0"
        Types = ModMenu.ModTypes.Gameplay
        SaveEnabledState = ModMenu.EnabledSaveType.LoadWithSettings

        def __init__(self):
            self.bridge: P.Bridge | None = None
            self.game = host.GameAdapter()
            self.runner: host.BridgeRunner | None = None
            self._thread: threading.Thread | None = None

        # -- lifecycle -------------------------------------------------------------------
        def Enable(self):
            self.bridge = P.Bridge.create()
            self.bridge.write_header(bl2_pid=os.getpid())
            self.runner = host.BridgeRunner(self.bridge, self.game)
            self._thread = threading.Thread(target=self.runner.run, name="BorderCraft", daemon=True)
            self._thread.start()
            unrealsdk.RegisterHook("WillowGame.WillowPlayerController.PlayerTick",
                                   "BorderCraft_Tick", self._on_tick)
            unrealsdk.RegisterHook("WillowGame.WillowPlayerController.InputKey",
                                   "BorderCraft_Input", self._on_input)
            unrealsdk.Log("[BorderCraft] bridge up at " + self.bridge.path)

        def Disable(self):
            try:
                unrealsdk.RemoveHook("WillowGame.WillowPlayerController.PlayerTick", "BorderCraft_Tick")
                unrealsdk.RemoveHook("WillowGame.WillowPlayerController.InputKey", "BorderCraft_Input")
            except Exception:
                pass
            if self.runner:
                self.runner.stop()
            if self.bridge:
                self.bridge.close()
                self.bridge = None
            unrealsdk.Log("[BorderCraft] bridge down")

        # -- engine hooks -----------------------------------------------------------------
        def _on_tick(self, caller, function, params):
            if self.runner:
                self.runner.on_engine_tick()
            return True

        def _on_input(self, caller, function, params):
            # Forward raw key/mouse to MC (BL2 keeps Esc/Tab/F/~). See host.InputBridge.
            if self.runner:
                self.runner.on_input(params)
            return True

    ModMenu.RegisterMod(BorderCraft())
