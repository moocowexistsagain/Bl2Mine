#!/usr/bin/env python3
"""Unit tests for the native-free BL2 Minecraft-skin Canvas renderer."""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bl2sdk.BorderCraft import render  # noqa: E402

P = render.P


class FakeOverlay:
    def __init__(self):
        self.frames = []

    def acquire(self):
        return self.frames.pop(0) if self.frames else None


class FakeBridge:
    def __init__(self):
        self.overlay = FakeOverlay()


class FakeCanvas:
    def __init__(self, width=800, height=600):
        self.ClipX = width
        self.ClipY = height
        self.color = None
        self.pos = None
        self.rects = []

    def SetDrawColor(self, *rgba):
        self.color = rgba

    def SetPos(self, x, y):
        self.pos = (x, y)

    def DrawRect(self, width, height, texture):
        self.rects.append((self.pos, width, height, self.color, texture))


def skin_with_red_face() -> bytes:
    pixels = bytearray(64 * 64 * 4)
    for y in range(8, 16):
        for x in range(8, 16):
            i = (y * 64 + x) * 4
            pixels[i:i + 4] = b"\x00\x00\xff\xff"  # BGRA red
    return bytes(pixels)


class SkinRendererTests(unittest.TestCase):
    def test_ignores_non_skin_overlay_payload(self):
        bridge = FakeBridge()
        renderer = render.OverlayCompositor(bridge, white_texture=object())
        bridge.overlay.frames.append((1, 64, 64, 0, skin_with_red_face()))
        renderer.on_engine_frame((0.0, 0.0, 0.0))

        canvas = FakeCanvas()
        self.assertFalse(renderer.draw_avatar(canvas))
        self.assertEqual(canvas.rects, [])

    def test_draws_bgra_skin_as_animated_ue_canvas_paper_doll(self):
        bridge = FakeBridge()
        texture = object()
        renderer = render.OverlayCompositor(bridge, white_texture=texture)
        bridge.overlay.frames.append(
            (7, 64, 64, P.OVERLAY_SKIN | P.OVERLAY_SLIM, skin_with_red_face())
        )
        renderer.on_engine_frame((1.0, 64.0, 2.0))
        renderer.on_engine_frame((1.2, 64.0, 2.0))

        canvas = FakeCanvas()
        self.assertTrue(renderer.draw_avatar(canvas))
        self.assertEqual(renderer.last_frame_id, 7)
        self.assertEqual(renderer.frames_drawn, 1)
        self.assertEqual(canvas.rects[0][3], (0, 0, 0, 105))  # backdrop
        red = [rect for rect in canvas.rects if rect[3] == (255, 0, 0, 255)]
        self.assertEqual(len(red), 8)  # one coalesced 8-pixel run per face row
        self.assertTrue(all(rect[1] == 8 * 5 for rect in red))
        self.assertTrue(all(rect[-1] is texture for rect in canvas.rects))

    def test_bottom_up_skin_rows_are_addressed_correctly(self):
        bridge = FakeBridge()
        renderer = render.OverlayCompositor(bridge, white_texture=object())
        pixels = bytearray(64 * 64 * 4)
        # Logical skin (8,8) is stored on physical row 55 when bottom-up.
        i = (55 * 64 + 8) * 4
        pixels[i:i + 4] = b"\x1e\x14\x0a\xff"
        bridge.overlay.frames.append(
            (3, 64, 64, P.OVERLAY_SKIN | P.OVERLAY_BOTTOM_UP, bytes(pixels))
        )
        renderer.poll()
        self.assertEqual(renderer._pixel(8, 8), (10, 20, 30, 255))


if __name__ == "__main__":
    unittest.main(verbosity=2)
