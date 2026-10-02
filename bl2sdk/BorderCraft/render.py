"""BorderCraft — frame compositor (BL2 side of the Phase 1a composite).

Reads Minecraft's rendered frames out of the shared-memory overlay double buffer and draws
them inside the Borderlands 2 window. The acquire path is real and protocol-tested
(tools/protocol_selftest.py, "overlay ok"); the UE3 draw call is the remaining in-game R&D.
"""
from __future__ import annotations

import bordercraft_protocol as P


class OverlayCompositor:
    """Owns the reader side of the overlay double buffer."""

    def __init__(self, bridge: P.Bridge):
        self.bridge = bridge
        self.frames_drawn = 0
        self.last_frame_id = 0

    def poll(self):
        """Newest Minecraft frame as (frame_id, w, h, flags, pixels), or None."""
        fr = self.bridge.overlay.acquire()
        if fr is not None:
            self.last_frame_id = fr[0]
        return fr

    def draw_fullscreen(self, frame) -> None:
        """Draw one acquired frame over BL2's viewport.

        TODO(Phase 1a, in-game R&D): get BGRA pixels onto a UE3 texture and blit it.
        Two candidate approaches through the Python SDK:

        1. Transient texture: construct a Texture2D, fill its first mip with the frame's
           bytes, draw it with WillowGame.WillowCanvas.DrawTile at full viewport.
        2. If the SDK's texture data isn't writable from Python: a tiny native helper that
           creates/updates a dynamic texture each frame (same bytes, same offsets).

        Both need experiment time inside the running game to find the exact SDK surface -
        that's the last unknown in Phase 1a. Everything around it is done.
        """
        raise NotImplementedError("UE3 draw hook pending in-game verification")

    def on_engine_frame(self) -> None:
        """Called from the BL2 engine tick: pull and draw the latest frame if any."""
        frame = self.poll()
        if frame is None:
            return
        try:
            self.draw_fullscreen(frame)
            self.frames_drawn += 1
        except NotImplementedError:
            pass  # until the draw hook lands, just draining frames keeps the pipeline live
