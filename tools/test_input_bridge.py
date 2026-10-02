#!/usr/bin/env python3
"""Unit tests for the Borderlands 2 -> Minecraft input bridge.

Borderlands 2 owns the window, so every keystroke is captured there and replayed into
Minecraft's key bindings. The things that matter are: the right keys get through in GLFW's
namespace, the keys Borderlands 2 needs for itself never do, and nothing is ever left held down
when a menu opens or the mod is turned off - otherwise the Minecraft player walks away on their
own while you read your inventory.
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bl2sdk.BorderCraft import inputmap  # noqa: E402

P = inputmap.P


class FakeKey:
    """A UE3 FName-alike: the SDK hands these to InputKey."""

    def __init__(self, name):
        self.Name = name


class Sink:
    def __init__(self, accept=True):
        self.events = []
        self.accept = accept

    def __call__(self, entry):
        self.events.append(entry)
        return self.accept

    def types(self):
        return [e.type for e in self.events]

    def codes(self):
        return [e.code for e in self.events]


def bridge(accept=True, sensitivity=1.0):
    sink = Sink(accept)
    clock = iter(range(1, 100000))
    return sink, inputmap.InputBridge(sink, clock=lambda: next(clock), sensitivity=sensitivity)


class NameTests(unittest.TestCase):
    def test_movement_keys_map_to_glfw(self):
        self.assertEqual(inputmap.key_code("W"), ord("W"))
        self.assertEqual(inputmap.key_code("A"), ord("A"))
        self.assertEqual(inputmap.key_code("SpaceBar"), inputmap.GLFW_SPACE)
        self.assertEqual(inputmap.key_code("LeftShift"), inputmap.GLFW_LEFT_SHIFT)
        self.assertEqual(inputmap.key_code("LeftControl"), inputmap.GLFW_LEFT_CONTROL)

    def test_hotbar_digits_map_to_glfw(self):
        for digit in range(1, 10):
            self.assertEqual(inputmap.key_code(str(digit)), ord(str(digit)))

    def test_names_are_case_and_whitespace_insensitive(self):
        self.assertEqual(inputmap.key_code(" w "), inputmap.key_code("W"))
        self.assertEqual(inputmap.key_code(FakeKey("SpaceBar")), inputmap.GLFW_SPACE)

    def test_borderlands_keeps_its_own_keys(self):
        for reserved in ("Escape", "Tab", "F", "Tilde", "F5", "F6", "F7"):
            self.assertIn(inputmap.normalize(reserved), inputmap.BL2_RESERVED, reserved)
            self.assertIsNone(inputmap.key_code(reserved), reserved)

    def test_unknown_keys_are_dropped(self):
        self.assertIsNone(inputmap.key_code("Gamepad_LeftThumbstick"))

    def test_mouse_buttons_map_to_glfw_button_numbers(self):
        self.assertEqual(inputmap.mouse_button("LeftMouseButton"), inputmap.GLFW_MOUSE_LEFT)
        self.assertEqual(inputmap.mouse_button("RightMouseButton"), inputmap.GLFW_MOUSE_RIGHT)
        self.assertEqual(inputmap.mouse_button("MiddleMouseButton"), inputmap.GLFW_MOUSE_MIDDLE)
        self.assertIsNone(inputmap.mouse_button("W"))


class KeyEventTests(unittest.TestCase):
    def test_a_press_and_release_round_trip(self):
        sink, b = bridge()
        self.assertTrue(b.on_input_key(FakeKey("W"), inputmap.IE_PRESSED))
        self.assertIn(ord("W"), b.held_keys)
        self.assertTrue(b.on_input_key(FakeKey("W"), inputmap.IE_RELEASED))
        self.assertNotIn(ord("W"), b.held_keys)
        self.assertEqual(sink.types(), [P.IN_KEY_DOWN, P.IN_KEY_UP])
        self.assertEqual(sink.codes(), [ord("W"), ord("W")])

    def test_key_repeats_are_dropped(self):
        sink, b = bridge()
        b.on_input_key(FakeKey("W"), inputmap.IE_PRESSED)
        self.assertFalse(b.on_input_key(FakeKey("W"), inputmap.IE_REPEAT))
        self.assertEqual(len(sink.events), 1, "Minecraft generates its own key repeats")

    def test_reserved_keys_never_reach_minecraft(self):
        sink, b = bridge()
        self.assertFalse(b.on_input_key(FakeKey("Escape"), inputmap.IE_PRESSED))
        self.assertEqual(sink.events, [])

    def test_mouse_buttons_become_mouse_events(self):
        sink, b = bridge()
        b.on_input_key(FakeKey("LeftMouseButton"), inputmap.IE_PRESSED)
        b.on_input_key(FakeKey("LeftMouseButton"), inputmap.IE_RELEASED)
        self.assertEqual(sink.types(), [P.IN_MOUSE_DOWN, P.IN_MOUSE_UP])
        self.assertEqual(sink.codes(), [inputmap.GLFW_MOUSE_LEFT] * 2)

    def test_a_double_click_counts_as_a_press(self):
        sink, b = bridge()
        b.on_input_key(FakeKey("LeftMouseButton"), inputmap.IE_DOUBLECLICK)
        self.assertEqual(sink.types(), [P.IN_MOUSE_DOWN])

    def test_the_wheel_scrolls_the_hotbar_in_both_directions(self):
        sink, b = bridge()
        b.on_input_key(FakeKey("MouseScrollUp"), inputmap.IE_PRESSED)
        b.on_input_key(FakeKey("MouseScrollDown"), inputmap.IE_PRESSED)
        self.assertEqual(sink.types(), [P.IN_MOUSE_WHEEL, P.IN_MOUSE_WHEEL])
        self.assertEqual([e.value for e in sink.events], [1, -1])

    def test_a_full_ring_is_counted_as_dropped(self):
        sink, b = bridge(accept=False)
        b.on_input_key(FakeKey("W"), inputmap.IE_PRESSED)
        self.assertEqual(b.dropped, 1)
        self.assertEqual(len(sink.events), 1)

    def test_string_event_names_are_tolerated(self):
        sink, b = bridge()
        b.on_input_key(FakeKey("W"), "Pressed")
        b.on_input_key(FakeKey("W"), "Released")
        self.assertEqual(sink.types(), [P.IN_KEY_DOWN, P.IN_KEY_UP])

    def test_every_event_is_timestamped(self):
        sink, b = bridge()
        b.on_input_key(FakeKey("W"), inputmap.IE_PRESSED)
        b.on_input_key(FakeKey("S"), inputmap.IE_PRESSED)
        stamps = [e.time_ms for e in sink.events]
        self.assertEqual(stamps, sorted(stamps))
        self.assertTrue(all(s > 0 for s in stamps))


class MouseTests(unittest.TestCase):
    def test_sub_pixel_motion_accumulates_instead_of_being_lost(self):
        sink, b = bridge()
        self.assertFalse(b.on_mouse_delta(0.4, 0.0))
        self.assertFalse(b.on_mouse_delta(0.4, 0.0))
        self.assertTrue(b.on_mouse_delta(0.4, 0.0))
        self.assertEqual(sink.events[0].value, 1)

    def test_sensitivity_scales_the_delta(self):
        sink, b = bridge(sensitivity=2.0)
        b.on_mouse_delta(3.0, -2.0)
        self.assertEqual((sink.events[0].value, sink.events[0].aux), (6, -4))

    def test_zero_motion_sends_nothing(self):
        sink, b = bridge()
        self.assertFalse(b.on_mouse_delta(0.0, 0.0))
        self.assertEqual(sink.events, [])


class FocusTests(unittest.TestCase):
    def test_release_all_lifts_every_held_input_and_signals_focus_loss(self):
        sink, b = bridge()
        b.on_input_key(FakeKey("W"), inputmap.IE_PRESSED)
        b.on_input_key(FakeKey("LeftShift"), inputmap.IE_PRESSED)
        b.on_input_key(FakeKey("LeftMouseButton"), inputmap.IE_PRESSED)
        sink.events.clear()

        b.release_all()
        self.assertEqual(b.held_keys, set())
        self.assertEqual(b.held_buttons, set())
        self.assertEqual(sink.types().count(P.IN_KEY_UP), 2)
        self.assertEqual(sink.types().count(P.IN_MOUSE_UP), 1)
        self.assertEqual(sink.types()[-1], P.IN_FOCUS_LOST)

    def test_suspending_releases_and_then_swallows_presses(self):
        sink, b = bridge()
        b.on_input_key(FakeKey("W"), inputmap.IE_PRESSED)
        b.set_suspended(True)
        self.assertIn(P.IN_KEY_UP, sink.types())

        sink.events.clear()
        self.assertFalse(b.on_input_key(FakeKey("W"), inputmap.IE_PRESSED))
        self.assertFalse(b.on_mouse_delta(10.0, 10.0))
        self.assertEqual(sink.events, [])

    def test_a_release_still_gets_through_while_suspended(self):
        # Otherwise a key held when the menu opened would stay down forever on the MC side.
        sink, b = bridge()
        b.set_suspended(True)
        sink.events.clear()
        self.assertTrue(b.on_input_key(FakeKey("W"), inputmap.IE_RELEASED))
        self.assertEqual(sink.types(), [P.IN_KEY_UP])

    def test_resuming_lets_input_through_again(self):
        sink, b = bridge()
        b.set_suspended(True)
        b.set_suspended(False)
        sink.events.clear()
        self.assertTrue(b.on_input_key(FakeKey("W"), inputmap.IE_PRESSED))

    def test_suspending_twice_is_idempotent(self):
        sink, b = bridge()
        b.set_suspended(True)
        count = len(sink.events)
        b.set_suspended(True)
        self.assertEqual(len(sink.events), count)


if __name__ == "__main__":
    unittest.main(verbosity=2)
