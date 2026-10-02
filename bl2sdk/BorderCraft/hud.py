"""The Minecraft HUD and inventory, drawn over Borderlands 2.

Minecraft owns the inventory — every slot, stack count and durability value here was computed by
Minecraft and shipped across as ``HudState``. This module only paints it: hearts, armour,
hunger, the XP bar, the hotbar, and the full inventory grid when a Minecraft screen is open.

Everything is pixel art assembled from axis-aligned rectangles, because that is the one drawing
primitive Unreal Engine 3's Canvas gives a Python mod. Rectangles are emitted through an
injected ``draw`` callable so the whole module is testable outside the game.
"""
from __future__ import annotations

try:
    from . import bordercraft_protocol as P
except ImportError:  # pragma: no cover - development checkout
    import bordercraft_protocol as P

# -- pixel art -------------------------------------------------------------------------------
# '.' transparent, '#' outline, 'r' body, 'w' highlight, 'd' shadow
HEART = (
    ".##...##.",
    "#rw#.#rr#",
    "#rww#rrr#",
    "#rrrrrrr#",
    ".#rrrrr#.",
    "..#rrr#..",
    "...#r#...",
    "....#....",
)
HEART_EMPTY = (
    ".##...##.",
    "#dd#.#dd#",
    "#ddd#ddd#",
    "#ddddddd#",
    ".#ddddd#.",
    "..#ddd#..",
    "...#d#...",
    "....#....",
)
ARMOR = (
    ".###.###.",
    "#rww#rrr#",
    "#rrrrrrr#",
    "#rrrrrrr#",
    "#rrrrrrr#",
    ".#rrrrr#.",
    ".#rrrrr#.",
    ".#######.",
)
FOOD = (
    "...###...",
    "..#rrw#..",
    ".#rrrrr#.",
    ".#rrrrr#.",
    "..#rrr#..",
    "...#r#...",
    "..##r##..",
    ".#.###.#.",
)
BUBBLE = (
    "...###...",
    "..#rrw#..",
    ".#rrrrw#.",
    ".#rrrrr#.",
    ".#rrrrr#.",
    "..#rrr#..",
    "...###...",
    ".........",
)

PALETTES = {
    "heart": {"#": (20, 0, 0, 255), "r": (220, 40, 40, 255), "w": (255, 150, 150, 255)},
    "heart_empty": {"#": (20, 20, 20, 220), "d": (60, 60, 60, 200)},
    "armor": {"#": (20, 20, 25, 255), "r": (190, 195, 205, 255), "w": (240, 245, 255, 255)},
    "food": {"#": (30, 18, 8, 255), "r": (200, 150, 70, 255), "w": (245, 210, 150, 255)},
    "bubble": {"#": (10, 25, 45, 255), "r": (120, 200, 255, 255), "w": (220, 245, 255, 255)},
}

# 3x5 digits, enough for stack counts and XP levels.
DIGITS = {
    "0": ("###", "#.#", "#.#", "#.#", "###"),
    "1": (".#.", "##.", ".#.", ".#.", "###"),
    "2": ("###", "..#", "###", "#..", "###"),
    "3": ("###", "..#", "###", "..#", "###"),
    "4": ("#.#", "#.#", "###", "..#", "..#"),
    "5": ("###", "#..", "###", "..#", "###"),
    "6": ("###", "#..", "###", "#.#", "###"),
    "7": ("###", "..#", "..#", "..#", "..#"),
    "8": ("###", "#.#", "###", "#.#", "###"),
    "9": ("###", "#.#", "###", "..#", "###"),
    "+": ("...", ".#.", "###", ".#.", "..."),
}


def draw_sprite(draw, art, palette, x: float, y: float, scale: int) -> None:
    """Blit pixel art, coalescing equal horizontal runs into single rectangles."""
    for row, line in enumerate(art):
        col = 0
        width = len(line)
        while col < width:
            ch = line[col]
            if ch == "." or ch not in palette:
                col += 1
                continue
            run = 1
            while col + run < width and line[col + run] == ch:
                run += 1
            draw(x + col * scale, y + row * scale, run * scale, scale, palette[ch])
            col += run


def draw_number(draw, text: str, x: float, y: float, scale: int,
                color=(255, 255, 255, 255), shadow=(0, 0, 0, 200)) -> float:
    """Draw digits with a Minecraft-style drop shadow. Returns the advance width."""
    cursor = x
    for ch in text:
        art = DIGITS.get(ch)
        if art is None:
            cursor += 4 * scale
            continue
        if shadow is not None:
            draw_sprite(draw, art, {"#": shadow}, cursor + scale, y + scale, scale)
        draw_sprite(draw, art, {"#": color}, cursor, y, scale)
        cursor += 4 * scale
    return cursor - x


def _shade(rgb: int, factor: float, alpha: int = 255):
    r = min(255, int(((rgb >> 16) & 0xFF) * factor))
    g = min(255, int(((rgb >> 8) & 0xFF) * factor))
    b = min(255, int((rgb & 0xFF) * factor))
    return (r, g, b, alpha)


class HudRenderer:
    """Paints a ``HudState`` with the Minecraft HUD layout."""

    SLOT = 20  # hotbar slot size in HUD pixels, as in vanilla

    def __init__(self, scale: int = 3):
        self.scale = max(1, int(scale))
        self.rects_drawn = 0

    def auto_scale(self, viewport_height: float) -> int:
        """Pick a Minecraft-like GUI scale for the current Borderlands 2 viewport."""
        self.scale = max(2, min(5, int(viewport_height // 260) + 1))
        return self.scale

    # -- pieces ---------------------------------------------------------------------------
    def draw_item(self, draw, slot: "P.HudSlot", x: float, y: float, scale: int) -> None:
        """A 16x16 item icon: block items get a little isometric cube, tools a flat swatch."""
        if not slot.item_hash:
            return
        size = 16 * scale
        rgb = slot.rgb & 0xFFFFFF
        if slot.flags & P.SLOT_BLOCK:
            # An isometric cube in a 16x16 item cell, shaded with Minecraft's own face
            # brightness, so a block in the hotbar reads the same way it does in the world.
            unit = size / 16.0
            for i in range(8):  # top face: a rhombus with corners at (8,0) (16,4) (8,8) (0,4)
                w = 4 * (i + 1) if i < 4 else 4 * (8 - i)
                draw(x + (8 - w * 0.5) * unit, y + i * unit, w * unit, unit, _shade(rgb, 1.0))
            for k in range(4):  # the two side faces, as slanted 2px columns
                top = (4 + 2 * k) * unit
                draw(x + 2 * k * unit, y + top, 2 * unit, 8 * unit, _shade(rgb, 0.6))
                draw(x + (14 - 2 * k) * unit, y + top, 2 * unit, 8 * unit, _shade(rgb, 0.8))
        else:
            draw(x + 2 * scale, y + 2 * scale, size - 4 * scale, size - 4 * scale, _shade(rgb, 1.0))
            draw(x + 2 * scale, y + 2 * scale, size - 4 * scale, scale, _shade(rgb, 1.3))
            draw(x + 2 * scale, y + size - 3 * scale, size - 4 * scale, scale, _shade(rgb, 0.6))
        if slot.flags & P.SLOT_ENCHANTED:
            draw(x + 2 * scale, y + 2 * scale, size - 4 * scale, scale, (190, 120, 255, 140))
        if slot.count > 1:
            text = str(min(slot.count, 999))
            width = len(text) * 4 * scale
            draw_number(draw, text, x + size - width - scale, y + size - 6 * scale, scale)
        if slot.flags & P.SLOT_DAMAGED and slot.damage:
            bar_w = size - 4 * scale
            left = max(0.0, 1.0 - slot.damage / 1000.0)
            draw(x + 2 * scale, y + size - 3 * scale, bar_w, scale, (0, 0, 0, 255))
            colour = (int(255 * (1.0 - left)), int(255 * left), 40, 255)
            draw(x + 2 * scale, y + size - 3 * scale, int(bar_w * left), scale, colour)

    def draw_slot_frame(self, draw, x: float, y: float, scale: int, selected: bool) -> None:
        size = self.SLOT * scale
        draw(x, y, size, size, (40, 40, 40, 190))
        draw(x, y, size, scale, (90, 90, 90, 220))
        draw(x, y, scale, size, (90, 90, 90, 220))
        draw(x, y + size - scale, size, scale, (20, 20, 20, 220))
        draw(x + size - scale, y, scale, size, (20, 20, 20, 220))
        if selected:
            pad = scale
            for edge in range(2):
                off = pad + edge * scale
                draw(x - off, y - off, size + 2 * off, scale, (255, 255, 255, 235))
                draw(x - off, y + size + off - scale, size + 2 * off, scale, (255, 255, 255, 235))
                draw(x - off, y - off, scale, size + 2 * off, (255, 255, 255, 235))
                draw(x + size + off - scale, y - off, scale, size + 2 * off, (255, 255, 255, 235))

    def draw_hotbar(self, draw, hud: "P.HudState", cx: float, bottom: float, scale: int) -> float:
        slots = hud.slots
        size = self.SLOT * scale
        total = size * 9
        x = cx - total * 0.5
        y = bottom - size
        for i in range(9):
            selected = (i == hud.selected_slot)
            self.draw_slot_frame(draw, x + i * size, y, scale, selected)
            if i < len(slots):
                self.draw_item(draw, slots[i], x + i * size + 2 * scale, y + 2 * scale, scale)
        return y

    def draw_bars(self, draw, hud: "P.HudState", cx: float, bottom: float, scale: int) -> None:
        """Hearts + armour on the left, hunger + air on the right, exactly like vanilla."""
        half = self.SLOT * scale * 4.5
        row_h = 10 * scale
        left = cx - half
        right = cx + half
        y = bottom - row_h

        max_health = max(1.0, hud.max_health)
        hearts = 10
        per_heart = max_health / hearts
        for i in range(hearts):
            x = left + i * 8 * scale
            filled = hud.health >= (i + 1) * per_heart - 1e-4
            partial = (not filled) and hud.health > i * per_heart
            draw_sprite(draw, HEART_EMPTY, PALETTES["heart_empty"], x, y, scale)
            if filled:
                draw_sprite(draw, HEART, PALETTES["heart"], x, y, scale)
            elif partial:
                # Half heart: clip by drawing only the left half of the sprite rows.
                art = tuple(line[:5] + "...." for line in HEART)
                draw_sprite(draw, art, PALETTES["heart"], x, y, scale)
        if hud.absorption > 0.0:
            draw(left, y - 2 * scale, int(8 * scale * min(10.0, hud.absorption / 2.0)),
                 scale, (240, 200, 60, 230))

        if hud.armor > 0.0:
            ay = y - row_h
            icons = int(round(min(20.0, hud.armor) / 2.0))
            for i in range(icons):
                draw_sprite(draw, ARMOR, PALETTES["armor"], left + i * 8 * scale, ay, scale)

        food_icons = int(round(min(20.0, hud.food) / 2.0))
        for i in range(food_icons):
            x = right - (i + 1) * 8 * scale
            draw_sprite(draw, FOOD, PALETTES["food"], x, y, scale)

        if hud.air < hud.max_air and hud.max_air > 0:
            bubbles = int(round(10.0 * hud.air / hud.max_air))
            by = y - row_h
            for i in range(bubbles):
                draw_sprite(draw, BUBBLE, PALETTES["bubble"], right - (i + 1) * 8 * scale, by, scale)

    def draw_xp(self, draw, hud: "P.HudState", cx: float, bottom: float, scale: int) -> None:
        width = self.SLOT * scale * 9
        height = 2 * scale
        x = cx - width * 0.5
        y = bottom
        draw(x, y, width, height, (20, 20, 20, 220))
        progress = max(0.0, min(1.0, hud.xp_progress))
        if progress > 0.0:
            draw(x, y, int(width * progress), height, (126, 252, 50, 255))
        if hud.xp_level > 0:
            text = str(hud.xp_level)
            draw_number(draw, text, cx - len(text) * 2 * scale, y - 7 * scale, scale,
                        color=(126, 252, 50, 255))

    def draw_inventory(self, draw, hud: "P.HudState", cx: float, cy: float, scale: int) -> None:
        """The 3x9 main grid, the hotbar row, armour and offhand — the Minecraft layout."""
        size = self.SLOT * scale
        grid_w = size * 9
        grid_h = size * 5 + 4 * scale
        pad = 6 * scale
        # The armour/offhand column lives to the left of the grid; the panel has to cover it.
        panel_x = cx - grid_w * 0.5 - size - pad
        panel_w = grid_w + size + pad
        x0 = cx - grid_w * 0.5 + size * 0.5
        y0 = cy - grid_h * 0.5
        draw(panel_x - pad + size * 0.5, y0 - pad, panel_w + 2 * pad, grid_h + 2 * pad,
             (18, 18, 20, 240))
        draw(panel_x - pad + size * 0.5, y0 - pad, panel_w + 2 * pad, scale, (110, 110, 115, 240))
        draw(panel_x - pad + size * 0.5, y0 + grid_h + pad - scale, panel_w + 2 * pad, scale,
             (110, 110, 115, 240))

        slots = hud.slots
        for index in range(9, 36):  # main inventory
            row, col = divmod(index - 9, 9)
            x = x0 + col * size
            y = y0 + row * size
            self.draw_slot_frame(draw, x, y, scale, False)
            if index < len(slots):
                self.draw_item(draw, slots[index], x + 2 * scale, y + 2 * scale, scale)
        for index in range(9):  # hotbar row
            x = x0 + index * size
            y = y0 + 3 * size + 4 * scale
            self.draw_slot_frame(draw, x, y, scale, index == hud.selected_slot)
            if index < len(slots):
                self.draw_item(draw, slots[index], x + 2 * scale, y + 2 * scale, scale)
        for i, index in enumerate(range(36, 40)):  # armour column
            x = x0 - size - pad
            y = y0 + i * size
            self.draw_slot_frame(draw, x, y, scale, False)
            if index < len(slots):
                self.draw_item(draw, slots[index], x + 2 * scale, y + 2 * scale, scale)
        if len(slots) > 40:  # offhand
            x = x0 - size - pad
            y = y0 + 4 * size + 4 * scale
            self.draw_slot_frame(draw, x, y, scale, False)
            self.draw_item(draw, slots[40], x + 2 * scale, y + 2 * scale, scale)

    # -- entry point ------------------------------------------------------------------------
    def render(self, draw, hud: "P.HudState", viewport: tuple[float, float]) -> int:
        """Draw the whole HUD. Returns the number of rectangles emitted."""
        counter = _Counter(draw)
        width, height = viewport
        scale = self.auto_scale(height)
        cx = width * 0.5
        margin = 8 * scale
        if hud.flags & P.HUD_SCREEN_OPEN:
            # Minecraft hides the in-game HUD behind an open screen; so do we.
            if hud.flags & P.HUD_INVENTORY_OPEN:
                self.draw_inventory(counter, hud, cx, height * 0.5, scale)
        else:
            hotbar_top = self.draw_hotbar(counter, hud, cx, height - margin, scale)
            self.draw_xp(counter, hud, cx, hotbar_top - 4 * scale, scale)
            self.draw_bars(counter, hud, cx, hotbar_top - 5 * scale, scale)
        self.rects_drawn = counter.count
        return counter.count


class _Counter:
    """Wraps a draw callable and counts the rectangles going through it."""

    __slots__ = ("draw", "count")

    def __init__(self, draw):
        self.draw = draw
        self.count = 0

    def __call__(self, x, y, w, h, color):
        if w <= 0 or h <= 0:
            return
        self.count += 1
        self.draw(x, y, w, h, color)
