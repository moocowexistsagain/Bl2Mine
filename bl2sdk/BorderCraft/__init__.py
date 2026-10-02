"""BorderCraft — the Borderlands 2 side of the shared-memory bridge.

This package supports both current willow2-sdk (``mods_base`` / ``build_mod``) and the older
``Mods.ModMenu.SDKMod`` API. For an installable package, see ``release/bordercraft-0.1.0.sdkmod``.
"""
from __future__ import annotations

import os
import sys
import threading

# A .sdkmod package bundles this sibling module. The source checkout keeps the protocol in its
# canonical location, protocol/python/, so add that path only for in-repo development.
_HERE = os.path.dirname(os.path.abspath(__file__))
try:
    from . import bordercraft_protocol as P
except ImportError:
    _PROTOCOL_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", "protocol", "python"))
    if os.path.isdir(_PROTOCOL_DIR) and _PROTOCOL_DIR not in sys.path:
        sys.path.insert(0, _PROTOCOL_DIR)
    import bordercraft_protocol as P  # noqa: E402

try:
    import unrealsdk  # type: ignore
except ImportError:
    unrealsdk = None

from . import host  # noqa: E402

_bridge: P.Bridge | None = None
_runner: host.BridgeRunner | None = None
_thread: threading.Thread | None = None


def _log(message: str) -> None:
    if unrealsdk is None:
        return
    text = "[BorderCraft] " + message
    try:
        # willow2-sdk 3.x exposes the standard logging module; the legacy SDK uses Log().
        from unrealsdk import logging

        logging.info(text)
    except (ImportError, AttributeError):
        try:
            unrealsdk.Log(text)
        except Exception:
            pass


def _start_bridge() -> None:
    """Start the bridge once when the mod is enabled."""
    global _bridge, _runner, _thread
    if _bridge is not None:
        return

    bridge = None
    runner = None
    thread = None
    try:
        bridge = P.Bridge.create()
        bridge.write_header(bl2_pid=os.getpid())
        runner = host.BridgeRunner(bridge, host.GameAdapter())
        thread = threading.Thread(target=runner.run, name="BorderCraft", daemon=True)

        # Publish before starting the worker so an engine callback always sees a complete session.
        _bridge, _runner, _thread = bridge, runner, thread
        thread.start()
        _log("bridge up at " + bridge.path)
    except Exception as exc:
        if runner is not None:
            runner.stop()
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        if bridge is not None:
            try:
                bridge.close()
            except Exception:
                pass
        _bridge = _runner = _thread = None
        _log("could not start bridge: " + repr(exc))


def _stop_bridge() -> None:
    """Stop the worker before closing its shared mapping."""
    global _bridge, _runner, _thread
    runner, thread, bridge = _runner, _thread, _bridge
    _runner = _thread = _bridge = None

    if runner is not None:
        try:
            runner.on_mod_disable()
        except Exception as exc:
            _log("error restoring avatar view: " + repr(exc))
        runner.stop()
    if thread is not None and thread.is_alive():
        thread.join(timeout=2.0)
    if bridge is not None:
        try:
            bridge.close()
        except Exception as exc:
            _log("error closing bridge: " + repr(exc))
    _log("bridge down")


if unrealsdk is not None:
    try:
        # Native API in willow2-sdk 3.x+. build_mod reads the display metadata from pyproject.toml
        # and makes the hooks below follow the enabled state in the in-game Mods menu.
        from mods_base import build_mod, hook
        from unrealsdk.hooks import Type
    except ImportError:
        # Compatibility path for the original PythonSDK / willow2-sdk ModMenu API.
        from Mods import ModMenu  # type: ignore

        class BorderCraft(ModMenu.SDKMod):
            Name = "BorderCraft"
            Author = "BorderCraft contributors"
            Description = (
                "Bridge Borderlands 2 to Minecraft. Gameplay integration is under development."
            )
            Version = "0.1.0"
            Types = ModMenu.ModTypes.Gameplay
            SaveEnabledState = ModMenu.EnabledSaveType.LoadWithSettings

            def Enable(self):
                _start_bridge()
                unrealsdk.RegisterHook(
                    "WillowGame.WillowPlayerController.PlayerTick",
                    "BorderCraft_Tick",
                    self._on_tick,
                )
                unrealsdk.RegisterHook(
                    "WillowGame.WillowPlayerController.InputKey",
                    "BorderCraft_Input",
                    self._on_input,
                )
                unrealsdk.RegisterHook(
                    "WillowGame.WillowGameViewportClient.PostRender",
                    "BorderCraft_RenderSkin",
                    self._on_post_render,
                )

            def Disable(self):
                for function, hook_id in (
                    ("WillowGame.WillowPlayerController.PlayerTick", "BorderCraft_Tick"),
                    ("WillowGame.WillowPlayerController.InputKey", "BorderCraft_Input"),
                    ("WillowGame.WillowGameViewportClient.PostRender", "BorderCraft_RenderSkin"),
                ):
                    try:
                        unrealsdk.RemoveHook(function, hook_id)
                    except Exception:
                        pass
                _stop_bridge()

            def _on_tick(self, caller, function, params):
                if _runner is not None:
                    _runner.on_engine_tick()
                return True

            def _on_input(self, caller, function, params):
                if _runner is not None:
                    _runner.on_input(params)
                return True

            def _on_post_render(self, caller, function, params):
                if _runner is not None:
                    _runner.on_post_render(getattr(params, "Canvas", None))
                return True

        ModMenu.RegisterMod(BorderCraft())
    else:
        # Current willow2-sdk hooks are registered with the mod and only run while it is enabled.
        @hook("WillowGame.WillowPlayerController:PlayerTick", Type.PRE)
        def _on_tick(caller, params, ret, function):
            if _runner is not None:
                _runner.on_engine_tick()

        @hook("WillowGame.WillowPlayerController:InputKey", Type.PRE)
        def _on_input(caller, params, ret, function):
            if _runner is not None:
                _runner.on_input(params)

        @hook("WillowGame.WillowGameViewportClient:PostRender", Type.POST)
        def _on_post_render(caller, params, ret, function):
            if _runner is not None:
                _runner.on_post_render(getattr(params, "Canvas", None))

        def on_enable() -> None:
            _start_bridge()

        def on_disable() -> None:
            _stop_bridge()

        BorderCraft = build_mod()
