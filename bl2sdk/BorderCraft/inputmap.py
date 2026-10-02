"""Translate Borderlands 2 (Unreal Engine 3) input events into Minecraft's key namespace.

Borderlands 2 owns the window, so every keystroke arrives as a UE3 ``FName`` through
``WillowPlayerController.InputKey``. Minecraft's key bindings are expressed in GLFW key codes,
so that is what goes over the wire: the Fabric side can then hand an event straight to
``KeyBinding`` without inventing a second translation table.

Keys Borderlands 2 keeps for itself (menu, map, console, the BorderCraft toggles) are listed in
:data:`BL2_RESERVED` and never forwarded.
"""
from __future__ import annotations

try:
    from . import bordercraft_protocol as P
except ImportError:  # pragma: no cover - development checkout
    import bordercraft_protocol as P

# GLFW key codes (the subset Minecraft binds by default).
GLFW_SPACE = 32
GLFW_APOSTROPHE = 39
GLFW_COMMA = 44
GLFW_MINUS = 45
GLFW_PERIOD = 46
GLFW_SLASH = 47
GLFW_SEMICOLON = 59
GLFW_EQUAL = 61
GLFW_LEFT_BRACKET = 91
GLFW_BACKSLASH = 92
GLFW_RIGHT_BRACKET = 93
GLFW_GRAVE = 96
GLFW_ESCAPE = 256
GLFW_ENTER = 257
GLFW_TAB = 258
GLFW_BACKSPACE = 259
GLFW_INSERT = 260
GLFW_DELETE = 261
GLFW_RIGHT = 262
GLFW_LEFT = 263
GLFW_DOWN = 264
GLFW_UP = 265
GLFW_PAGE_UP = 266
GLFW_PAGE_DOWN = 267
GLFW_HOME = 268
GLFW_END = 269
GLFW_F1 = 290
GLFW_KP_0 = 320
GLFW_LEFT_SHIFT = 340
GLFW_LEFT_CONTROL = 341
GLFW_LEFT_ALT = 342
GLFW_RIGHT_SHIFT = 344
GLFW_RIGHT_CONTROL = 345
GLFW_RIGHT_ALT = 346

GLFW_MOUSE_LEFT = 0
GLFW_MOUSE_RIGHT = 1
GLFW_MOUSE_MIDDLE = 2
GLFW_MOUSE_4 = 3
GLFW_MOUSE_5 = 4


def _letters_and_digits() -> dict[str, int]:
    table = {}
    for i in range(26):
        table[chr(ord("A") + i)] = 65 + i  # GLFW_KEY_A .. GLFW_KEY_Z
    for name, digit in (("ZERO", 0), ("ONE", 1), ("TWO", 2), ("THREE", 3), ("FOUR", 4),
                        ("FIVE", 5), ("SIX", 6), ("SEVEN", 7), ("EIGHT", 8), ("NINE", 9)):
        table[name] = 48 + digit
        table[str(digit)] = 48 + digit
    for i in range(10):
        table[f"NUMPADZERO" if i == 0 else f"NUMPAD{i}"] = GLFW_KP_0 + i
    for i in range(1, 13):
        table[f"F{i}"] = GLFW_F1 + (i - 1)
    return table


KEY_NAMES: dict[str, int] = _letters_and_digits()
KEY_NAMES.update({
    "SPACEBAR": GLFW_SPACE,
    "SPACE": GLFW_SPACE,
    "LEFTSHIFT": GLFW_LEFT_SHIFT,
    "RIGHTSHIFT": GLFW_RIGHT_SHIFT,
    "LEFTCONTROL": GLFW_LEFT_CONTROL,
    "RIGHTCONTROL": GLFW_RIGHT_CONTROL,
    "LEFTALT": GLFW_LEFT_ALT,
    "RIGHTALT": GLFW_RIGHT_ALT,
    "LEFTSHIFTKEY": GLFW_LEFT_SHIFT,
    "ENTER": GLFW_ENTER,
    "RETURN": GLFW_ENTER,
    "BACKSPACE": GLFW_BACKSPACE,
    "DELETE": GLFW_DELETE,
    "INSERT": GLFW_INSERT,
    "HOME": GLFW_HOME,
    "END": GLFW_END,
    "PAGEUP": GLFW_PAGE_UP,
    "PAGEDOWN": GLFW_PAGE_DOWN,
    "UP": GLFW_UP,
    "DOWN": GLFW_DOWN,
    "LEFT": GLFW_LEFT,
    "RIGHT": GLFW_RIGHT,
    "COMMA": GLFW_COMMA,
    "PERIOD": GLFW_PERIOD,
    "SLASH": GLFW_SLASH,
    "SEMICOLON": GLFW_SEMICOLON,
    "QUOTE": GLFW_APOSTROPHE,
    "APOSTROPHE": GLFW_APOSTROPHE,
    "LEFTBRACKET": GLFW_LEFT_BRACKET,
    "RIGHTBRACKET": GLFW_RIGHT_BRACKET,
    "BACKSLASH": GLFW_BACKSLASH,
    "EQUALS": GLFW_EQUAL,
    "ADD": GLFW_EQUAL,
    "UNDERSCORE": GLFW_MINUS,
    "SUBTRACT": GLFW_MINUS,
    "HYPHEN": GLFW_MINUS,
    "MINUS": GLFW_MINUS,
})

MOUSE_NAMES: dict[str, int] = {
    "LEFTMOUSEBUTTON": GLFW_MOUSE_LEFT,
    "RIGHTMOUSEBUTTON": GLFW_MOUSE_RIGHT,
    "MIDDLEMOUSEBUTTON": GLFW_MOUSE_MIDDLE,
    "THUMBMOUSEBUTTON": GLFW_MOUSE_4,
    "THUMBMOUSEBUTTON2": GLFW_MOUSE_5,
}

WHEEL_NAMES = {"MOUSESCROLLUP": 1, "MOUSESCROLLDOWN": -1}
AXIS_NAMES = {"MOUSEX": 0, "MOUSEY": 1}

# Borderlands 2 keeps these: its menus, map, console and the BorderCraft view toggle.
BL2_RESERVED = frozenset({"ESCAPE", "TAB", "F", "TILDE", "F10", "ALT", "F4",
                          # BorderCraft's own toggles: avatar, physics authority, HUD.
                          "F5", "F6", "F7"})

# UE3 input event ids.
IE_PRESSED, IE_RELEASED, IE_REPEAT, IE_DOUBLECLICK, IE_AXIS = 0, 1, 2, 3, 4


def normalize(name) -> str:
    return str(getattr(name, "Name", name)).strip().upper()


def key_code(name) -> int | None:
    """GLFW key code for a UE3 key name, or ``None`` when Minecraft should not see it."""
    key = normalize(name)
    if key in BL2_RESERVED:
        return None
    return KEY_NAMES.get(key)


def mouse_button(name) -> int | None:
    return MOUSE_NAMES.get(normalize(name))


class InputBridge:
    """Turns UE3 ``InputKey`` parameters into protocol ``InputEntry`` records.

    Held keys are tracked so a Borderlands 2 menu, a loading screen or an alt-tab can release
    everything at once — otherwise Minecraft would keep walking while the player is in a menu.
    """

    def __init__(self, push, clock=None, sensitivity: float = 1.0):
        self.push = push
        self.clock = clock or P.monotonic_ms
        self.sensitivity = float(sensitivity)
        self.held_keys: set[int] = set()
        self.held_buttons: set[int] = set()
        self.suspended = False
        self.dropped = 0
        self._pending_dx = 0.0
        self._pending_dy = 0.0

    # -- events ---------------------------------------------------------------------------
    def _emit(self, kind: int, code: int, value: int = 0, aux: int = 0) -> bool:
        ok = self.push(P.InputEntry(kind, code, value, aux, self.clock() & 0xFFFFFFFF))
        if not ok:
            self.dropped += 1
        return ok

    def on_input_key(self, key, event) -> bool:
        """Handle one ``WillowPlayerController.InputKey``. Returns True when forwarded."""
        name = normalize(key)
        try:
            event_id = int(event)
        except (TypeError, ValueError):
            event_id = IE_PRESSED if "PRESSED" in normalize(event) else IE_RELEASED
        if event_id == IE_REPEAT:
            return False  # Minecraft generates its own repeats from the held state

        pressed = event_id in (IE_PRESSED, IE_DOUBLECLICK)
        if name in WHEEL_NAMES:
            if pressed:
                return self._emit(P.IN_MOUSE_WHEEL, 0, WHEEL_NAMES[name])
            return False

        button = MOUSE_NAMES.get(name)
        if button is not None:
            if self.suspended and pressed:
                return False
            if pressed:
                self.held_buttons.add(button)
            else:
                self.held_buttons.discard(button)
            return self._emit(P.IN_MOUSE_DOWN if pressed else P.IN_MOUSE_UP, button)

        code = key_code(name)
        if code is None:
            return False
        if self.suspended and pressed:
            return False
        if pressed:
            self.held_keys.add(code)
        else:
            self.held_keys.discard(code)
        return self._emit(P.IN_KEY_DOWN if pressed else P.IN_KEY_UP, code)

    def on_mouse_delta(self, dx: float, dy: float) -> bool:
        """Accumulate sub-pixel mouse motion and forward whole counts."""
        if self.suspended:
            return False
        self._pending_dx += dx * self.sensitivity
        self._pending_dy += dy * self.sensitivity
        ix = int(self._pending_dx)
        iy = int(self._pending_dy)
        if ix == 0 and iy == 0:
            return False
        self._pending_dx -= ix
        self._pending_dy -= iy
        return self._emit(P.IN_MOUSE_MOVE, 0, ix, iy)

    # -- focus ----------------------------------------------------------------------------
    def release_all(self) -> None:
        """Drop every held key/button (menu opened, map opened, focus lost, mod disabled)."""
        for code in sorted(self.held_keys):
            self._emit(P.IN_KEY_UP, code)
        for button in sorted(self.held_buttons):
            self._emit(P.IN_MOUSE_UP, button)
        self.held_keys.clear()
        self.held_buttons.clear()
        self._pending_dx = self._pending_dy = 0.0
        self._emit(P.IN_FOCUS_LOST, 0)

    def set_suspended(self, suspended: bool) -> None:
        """Suspend forwarding while Borderlands 2 owns input; releases held keys on entry."""
        suspended = bool(suspended)
        if suspended == self.suspended:
            return
        self.suspended = suspended
        if suspended:
            self.release_all()
