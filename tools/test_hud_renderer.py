#!/usr/bin/env python3
"""Unit tests for the Minecraft HUD and inventory drawn over Borderlands 2.

Borderlands 2 cannot read Minecraft's item atlas, so the whole HUD - hearts, hunger, armour,
air, XP, the hotbar and the 41-slot inventory - is drawn from pure data with Canvas rectangles.
These tests check the arithmetic that decides what appears: how many hearts for a given health,
that a damaged tool gets a bar, that the selected slot is highlighted, and that the frame stays
inside the rectangle budget at every GUI scale.
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bl2sdk.BorderCraft import hud as hudmod  # noqa: E402

P = hudmod.P


class Recorder:
    """A ``draw(x, y, w, h, rgba)`` sink that remembers everything."""

    def __init__(self):
        self.rects = []

    def __call__(self, x, y, w, h, colour):
        self.rects.append((x, y, w, h, colour))

    def __len__(self):
        return len(self.rects)

    def bounds(self):
        xs = [r[0] for r in self.rects] + [r[0] + r[2] for r in self.rects]
        ys = [r[1] for r in self.rects] + [r[1] + r[3] for r in self.rects]
        return min(xs), min(ys), max(xs), max(ys)

    def colours(self):
        return {r[4][:3] for r in self.rects}


def full_hud(**overrides):
    slots = []
    for i in range(41):
        slots.append(P.HudSlot(item_hash=i + 1, count=(i % 60) + 1,
                               rgb=0x3C8F3C + i * 1013,
                               flags=P.SLOT_BLOCK | (P.SLOT_SELECTED if i == 3 else 0),
                               name="Item %d" % i))
    state = dict(health=20.0, max_health=20.0, food=20.0, air=300.0, max_air=300.0,
                 xp_level=7, xp_progress=0.5, selected_slot=3, slots=slots)
    state.update(overrides)
    return P.HudState(**state)


class SpriteTests(unittest.TestCase):
    def test_digits_cover_every_numeral(self):
        for ch in "0123456789":
            self.assertIn(ch, hudmod.DIGITS, f"no glyph for {ch}")
            self.assertEqual(len(hudmod.DIGITS[ch]), 5, ch)
            for row in hudmod.DIGITS[ch]:
                self.assertEqual(len(row), 3, ch)

    def test_draw_number_emits_rectangles_and_advances_right(self):
        draw = Recorder()
        hudmod.draw_number(draw, "42", 100.0, 50.0, 2)
        self.assertTrue(draw.rects)
        left, _top, right, _bottom = draw.bounds()
        self.assertGreaterEqual(left, 100.0 - 4)
        self.assertGreater(right, left)

    def test_draw_number_ignores_unknown_glyphs(self):
        draw = Recorder()
        hudmod.draw_number(draw, "?", 0.0, 0.0, 2)
        self.assertEqual(len(draw), 0)

    def test_shade_scales_rgb_and_keeps_alpha(self):
        r, g, b, a = hudmod._shade(0x804020, 0.5, 200)
        self.assertEqual((r, g, b, a), (0x40, 0x20, 0x10, 200))


class ScaleTests(unittest.TestCase):
    def test_auto_scale_grows_with_the_viewport_and_stays_in_range(self):
        renderer = hudmod.HudRenderer()
        small = renderer.auto_scale(480)
        large = renderer.auto_scale(2160)
        self.assertLessEqual(small, large)
        for height in (240, 720, 1080, 1440, 2160, 4320):
            scale = renderer.auto_scale(height)
            self.assertGreaterEqual(scale, 2)
            self.assertLessEqual(scale, 5)


class BarTests(unittest.TestCase):
    def test_full_health_draws_ten_hearts_worth_of_sprites(self):
        renderer = hudmod.HudRenderer()
        full = Recorder()
        renderer.draw_bars(full, P.HudState(health=20.0, food=0.0, air=300.0, max_air=300.0),
                           800.0, 900.0, 3)
        half = Recorder()
        renderer.draw_bars(half, P.HudState(health=10.0, food=0.0, air=300.0, max_air=300.0),
                           800.0, 900.0, 3)
        self.assertGreater(len(full), len(half),
                           "a hurt player must draw fewer filled hearts")

    def test_absorption_and_armour_add_to_the_bars(self):
        renderer = hudmod.HudRenderer()
        plain = Recorder()
        renderer.draw_bars(plain, P.HudState(health=20.0, food=20.0, air=300.0, max_air=300.0),
                           800.0, 900.0, 3)
        armoured = Recorder()
        renderer.draw_bars(armoured, P.HudState(health=20.0, food=20.0, armor=20.0,
                                                absorption=8.0, air=300.0, max_air=300.0),
                           800.0, 900.0, 3)
        self.assertGreater(len(armoured), len(plain))

    def test_air_bubbles_appear_only_underwater(self):
        renderer = hudmod.HudRenderer()
        surfaced = Recorder()
        renderer.draw_bars(surfaced, P.HudState(air=300.0, max_air=300.0), 800.0, 900.0, 3)
        drowning = Recorder()
        renderer.draw_bars(drowning, P.HudState(air=120.0, max_air=300.0), 800.0, 900.0, 3)
        self.assertGreater(len(drowning), len(surfaced))

    def test_xp_bar_width_tracks_progress(self):
        renderer = hudmod.HudRenderer()
        empty = Recorder()
        renderer.draw_xp(empty, P.HudState(xp_level=0, xp_progress=0.0), 800.0, 900.0, 3)
        full = Recorder()
        renderer.draw_xp(full, P.HudState(xp_level=30, xp_progress=1.0), 800.0, 900.0, 3)
        self.assertGreater(len(full), len(empty))


class ItemTests(unittest.TestCase):
    def test_an_empty_slot_draws_nothing_but_its_frame(self):
        renderer = hudmod.HudRenderer()
        draw = Recorder()
        renderer.draw_item(draw, P.HudSlot(), 0.0, 0.0, 3)
        self.assertEqual(len(draw), 0)

    def test_a_block_item_is_drawn_as_an_isometric_cube(self):
        renderer = hudmod.HudRenderer()
        cube = Recorder()
        renderer.draw_item(cube, P.HudSlot(item_hash=1, count=1, rgb=0x40A040,
                                           flags=P.SLOT_BLOCK), 0.0, 0.0, 3)
        flat = Recorder()
        renderer.draw_item(flat, P.HudSlot(item_hash=1, count=1, rgb=0x40A040), 0.0, 0.0, 3)
        self.assertGreater(len(cube), len(flat),
                           "a block icon has three shaded faces, a plain item does not")
        # Three faces means three distinct shades of the same hue.
        self.assertGreaterEqual(len(cube.colours()), 3)

    def test_a_stack_bigger_than_one_shows_its_count(self):
        renderer = hudmod.HudRenderer()
        single = Recorder()
        renderer.draw_item(single, P.HudSlot(item_hash=1, count=1, rgb=0x808080), 0.0, 0.0, 3)
        many = Recorder()
        renderer.draw_item(many, P.HudSlot(item_hash=1, count=64, rgb=0x808080), 0.0, 0.0, 3)
        self.assertGreater(len(many), len(single))

    def test_a_damaged_tool_gets_a_durability_bar(self):
        renderer = hudmod.HudRenderer()
        fresh = Recorder()
        renderer.draw_item(fresh, P.HudSlot(item_hash=1, count=1, rgb=0x808080), 0.0, 0.0, 3)
        worn = Recorder()
        renderer.draw_item(worn, P.HudSlot(item_hash=1, count=1, rgb=0x808080,
                                           flags=P.SLOT_DAMAGED, damage=7000), 0.0, 0.0, 3)
        self.assertGreater(len(worn), len(fresh))

    def test_the_selected_slot_frame_differs(self):
        renderer = hudmod.HudRenderer()
        plain = Recorder()
        renderer.draw_slot_frame(plain, 0.0, 0.0, 3, False)
        chosen = Recorder()
        renderer.draw_slot_frame(chosen, 0.0, 0.0, 3, True)
        self.assertNotEqual(plain.rects, chosen.rects)


class FrameTests(unittest.TestCase):
    def test_a_full_hud_frame_stays_inside_the_canvas_budget(self):
        renderer = hudmod.HudRenderer()
        for width, height in ((1280, 720), (1920, 1080), (2560, 1440), (3840, 2160)):
            draw = Recorder()
            count = renderer.render(draw, full_hud(), (width, height))
            self.assertEqual(count, len(draw))
            self.assertGreater(count, 0)
            self.assertLess(count, 1400,
                            f"{count} rectangles at {width}x{height} is over budget")

    def test_the_hud_is_drawn_inside_the_viewport(self):
        renderer = hudmod.HudRenderer()
        draw = Recorder()
        renderer.render(draw, full_hud(), (1920, 1080))
        left, top, right, bottom = draw.bounds()
        self.assertGreaterEqual(left, -2)
        self.assertGreaterEqual(top, -2)
        self.assertLessEqual(right, 1922)
        self.assertLessEqual(bottom, 1082)

    def test_an_open_screen_replaces_the_hud_the_way_minecraft_does(self):
        renderer = hudmod.HudRenderer()
        in_game = Recorder()
        renderer.render(in_game, full_hud(), (1920, 1080))
        self.assertGreater(len(in_game), 0)

        # A screen with no inventory (chat, pause): Minecraft hides the HUD, so do we.
        chatting = Recorder()
        renderer.render(chatting, full_hud(flags=P.HUD_SCREEN_OPEN), (1920, 1080))
        self.assertEqual(len(chatting), 0)

        # The inventory screen: the 41-slot grid replaces the hotbar, and costs more.
        opened = Recorder()
        renderer.render(opened, full_hud(flags=P.HUD_SCREEN_OPEN | P.HUD_INVENTORY_OPEN),
                        (1920, 1080))
        self.assertGreater(len(opened), len(in_game))

    def test_a_hud_with_no_items_still_renders(self):
        renderer = hudmod.HudRenderer()
        draw = Recorder()
        count = renderer.render(draw, P.HudState(slots=[]), (1920, 1080))
        self.assertGreater(count, 0)

    def test_a_degenerate_viewport_is_survivable(self):
        renderer = hudmod.HudRenderer()
        draw = Recorder()
        renderer.render(draw, full_hud(), (1, 1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
