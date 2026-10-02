"""The real 3D Minecraft player model, rendered inside Borderlands 2.

This is the full vanilla geometry — six boxes plus the six overlay ("hat"/"jacket") boxes, wide
or slim arms, textured from the authenticated 64x64 skin — posed by the vanilla biped rig and
rasterized by :mod:`raster` into Canvas spans.

Minecraft stays authoritative. It owns the physics and therefore every animation input
(``PoseState``: body yaw, head yaw/pitch, limb swing, hand swing, sneak blend); this module only
evaluates the rig and draws it. Nothing here guesses at where the player is or how it moves.

Model space
-----------
Right-handed, 16 units per block, the player 32 units tall:

* ``+X`` the player's right, ``+Y`` up (feet at ``y = 0``), ``+Z`` backwards (the model faces ``-Z``)

The renderer frames the model so that the bottom edge of the output buffer is the feet plane and
the horizontal centre is the body axis, which is exactly what the Borderlands 2 side needs to pin
the avatar onto a projected pawn.
"""
from __future__ import annotations

import math

from .raster import (
    Framebuffer,
    Rasterizer,
    Texture,
    mat_apply,
    mat_mul,
    rotation_matrix,
    IDENTITY,
)

try:
    from . import bordercraft_protocol as P
except ImportError:  # pragma: no cover - development checkout
    import bordercraft_protocol as P

# Minecraft's directional face shading (the same constants the vanilla block renderer uses).
SHADE_UP = 1.0
SHADE_DOWN = 0.5
SHADE_NORTH_SOUTH = 0.8
SHADE_EAST_WEST = 0.6

FACE_SHADE = {
    "top": SHADE_UP,
    "bottom": SHADE_DOWN,
    "front": SHADE_NORTH_SOUTH,
    "back": SHADE_NORTH_SOUTH * 0.85,
    "right": SHADE_EAST_WEST,
    "left": SHADE_EAST_WEST,
}

# Camera distance in model units (16 = one block). Large enough that the perspective is gentle
# and the avatar matches the shape Borderlands 2's own camera gives the pawn behind it.
CAMERA_DISTANCE = 200.0

# The model is 32 units tall; framing 34 leaves room for the inflated hat layer and for the
# slight perspective enlargement of the parts nearer than the camera plane. Centring on 17
# keeps the feet exactly on the bottom edge of the output, which is how the Borderlands 2 side
# pins the avatar to the projected feet of the pawn.
MODEL_FRAME_UNITS = 34.0
MODEL_CENTER_Y = 17.0


class Box:
    """One cuboid of the player model, with its vanilla skin unwrap."""

    __slots__ = ("name", "part", "origin", "size", "uv", "inflate", "overlay")

    def __init__(self, name, part, origin, size, uv, inflate=0.0, overlay=False):
        self.name = name
        self.part = part          # rig node that transforms it
        self.origin = origin      # min corner, relative to the part pivot
        self.size = size          # (sx, sy, sz) in texels == model units
        self.uv = uv              # top-left of the box unwrap in the 64x64 skin
        self.inflate = inflate
        self.overlay = overlay

    def faces(self):
        """Yield ``(face_name, [corners], [uvs])`` in model space, relative to the pivot."""
        sx, sy, sz = self.size
        ox, oy, oz = self.origin
        i = self.inflate
        x0, x1 = ox - i, ox + sx + i
        y0, y1 = oy - i, oy + sy + i
        z0, z1 = oz - i, oz + sz + i
        u, v = self.uv

        def rect(tu, tv, tw, th):
            # Half-texel inset keeps nearest sampling from bleeding into the neighbouring face.
            e = 0.002
            return [(tu + e, tv + e), (tu + tw - e, tv + e),
                    (tu + tw - e, tv + th - e), (tu + e, tv + th - e)]

        yield ("top", [(x1, y1, z1), (x0, y1, z1), (x0, y1, z0), (x1, y1, z0)],
               rect(u + sz, v, sx, sz))
        yield ("bottom", [(x1, y0, z0), (x0, y0, z0), (x0, y0, z1), (x1, y0, z1)],
               rect(u + sz + sx, v, sx, sz))
        yield ("right", [(x1, y1, z1), (x1, y1, z0), (x1, y0, z0), (x1, y0, z1)],
               rect(u, v + sz, sz, sy))
        yield ("front", [(x1, y1, z0), (x0, y1, z0), (x0, y0, z0), (x1, y0, z0)],
               rect(u + sz, v + sz, sx, sy))
        yield ("left", [(x0, y1, z0), (x0, y1, z1), (x0, y0, z1), (x0, y0, z0)],
               rect(u + sz + sx, v + sz, sz, sy))
        yield ("back", [(x0, y1, z1), (x1, y1, z1), (x1, y0, z1), (x0, y0, z1)],
               rect(u + sz + sx + sz, v + sz, sx, sy))


# Rig pivots, in model space. The body is the parent of the head and both arms so a crouch or a
# swim lean carries the whole upper body, exactly like Minecraft's own hierarchy does.
PIVOTS = {
    "body": (0.0, 12.0, 0.0),
    "head": (0.0, 24.0, 0.0),
    "right_arm": (5.0, 22.0, 0.0),
    "left_arm": (-5.0, 22.0, 0.0),
    "right_leg": (2.0, 12.0, 0.0),
    "left_leg": (-2.0, 12.0, 0.0),
}
PARENTS = {
    "body": None,
    "head": "body",
    "right_arm": "body",
    "left_arm": "body",
    "right_leg": None,
    "left_leg": None,
}


def build_boxes(slim: bool) -> list[Box]:
    """The twelve vanilla player boxes. ``slim`` switches to the 3px (Alex) arms."""
    arm_w = 3 if slim else 4
    # Arms hang off the shoulder pivots at x = +-5, inner edge flush with the torso, so a slim
    # arm simply loses one texel on its outer side exactly like the Alex model does.
    right_arm_origin = (-1.0, -10.0, -2.0)
    left_arm_origin = (-arm_w + 1.0, -10.0, -2.0)
    boxes = [
        Box("head", "head", (-4.0, 0.0, -4.0), (8, 8, 8), (0, 0)),
        Box("body", "body", (-4.0, 0.0, -2.0), (8, 12, 4), (16, 16)),
        Box("right_arm", "right_arm", right_arm_origin, (arm_w, 12, 4), (40, 16)),
        Box("left_arm", "left_arm", left_arm_origin, (arm_w, 12, 4), (32, 48)),
        Box("right_leg", "right_leg", (-2.0, -12.0, -2.0), (4, 12, 4), (0, 16)),
        Box("left_leg", "left_leg", (-2.0, -12.0, -2.0), (4, 12, 4), (16, 48)),
        # Overlay ("second layer") boxes, inflated the same amounts vanilla uses.
        Box("hat", "head", (-4.0, 0.0, -4.0), (8, 8, 8), (32, 0), inflate=0.5, overlay=True),
        Box("jacket", "body", (-4.0, 0.0, -2.0), (8, 12, 4), (16, 32), inflate=0.25, overlay=True),
        Box("right_sleeve", "right_arm", right_arm_origin, (arm_w, 12, 4), (40, 32),
            inflate=0.25, overlay=True),
        Box("left_sleeve", "left_arm", left_arm_origin, (arm_w, 12, 4), (48, 48),
            inflate=0.25, overlay=True),
        Box("right_pants", "right_leg", (-2.0, -12.0, -2.0), (4, 12, 4), (0, 32),
            inflate=0.25, overlay=True),
        Box("left_pants", "left_leg", (-2.0, -12.0, -2.0), (4, 12, 4), (0, 48),
            inflate=0.25, overlay=True),
    ]
    return boxes


_BOXES_WIDE = build_boxes(False)
_BOXES_SLIM = build_boxes(True)


def _wrap(angle: float) -> float:
    return (angle + 180.0) % 360.0 - 180.0


class RigPose:
    """Evaluated rig: per-part ``(pivot, 3x3 rotation)`` plus a whole-model offset."""

    __slots__ = ("parts", "offset", "slim", "main_hand_left")

    def __init__(self, parts, offset, slim, main_hand_left=False):
        self.parts = parts
        self.offset = offset
        self.slim = slim
        self.main_hand_left = main_hand_left


def evaluate_rig(pose: "P.PoseState") -> RigPose:
    """Evaluate Minecraft's biped animation from the published pose inputs.

    The formulas are the vanilla ``BipedEntityModel.setAngles`` ones: Minecraft publishes the
    inputs (limb swing, hand swing, sneak blend), and the walk cycle, head tracking and attack
    swing come out identical to what the Minecraft client is drawing offscreen.
    """
    limb = pose.limb_swing
    amount = max(0.0, min(1.0, pose.limb_swing_amount))
    sneak = max(0.0, min(1.0, pose.sneak_amount))
    swing = max(0.0, min(1.0, pose.hand_swing))

    head_yaw = math.radians(max(-75.0, min(75.0, _wrap(pose.head_yaw - pose.body_yaw))))
    head_pitch = math.radians(max(-90.0, min(90.0, pose.head_pitch)))

    right_arm = [math.cos(limb * 0.6662 + math.pi) * 2.0 * amount * 0.5, 0.0, 0.0]
    left_arm = [math.cos(limb * 0.6662) * 2.0 * amount * 0.5, 0.0, 0.0]
    right_leg = [math.cos(limb * 0.6662) * 1.4 * amount, 0.0, 0.0]
    left_leg = [math.cos(limb * 0.6662 + math.pi) * 1.4 * amount, 0.0, 0.0]

    # Holding something tucks the arm up, like vanilla's ITEM arm pose.
    if pose.held_main_rgb:
        if pose.flags & P.POSE_MAIN_HAND_LEFT:
            left_arm[0] = left_arm[0] * 0.5 - 0.6
        else:
            right_arm[0] = right_arm[0] * 0.5 - 0.6

    if swing > 0.0:
        progress = 1.0 - swing
        progress = 1.0 - progress * progress * progress
        bend = math.sin(progress * math.pi)
        reach = math.sin(swing * math.pi) * (-head_pitch - 0.7) * 0.75
        arm = left_arm if (pose.flags & P.POSE_MAIN_HAND_LEFT) else right_arm
        arm[0] -= bend * 1.2 + reach
        arm[1] += math.sin(swing * math.pi) * 0.4
        arm[2] += math.sin(swing * math.pi) * -0.4

    # Crouching and swimming both lean the upper body around the hips.
    body_pitch = 0.5 * sneak + math.radians(pose.lean_angle)
    if sneak > 0.0:
        right_arm[0] += 0.4 * sneak
        left_arm[0] += 0.4 * sneak

    rotations = {
        "body": (body_pitch, 0.0, 0.0),
        "head": (head_pitch, head_yaw, 0.0),
        "right_arm": tuple(right_arm),
        "left_arm": tuple(left_arm),
        "right_leg": tuple(right_leg),
        "left_leg": tuple(left_leg),
    }

    parts = {}
    for name in ("body", "head", "right_arm", "left_arm", "right_leg", "left_leg"):
        pitch, yaw, roll = rotations[name]
        # Minecraft's model space is this one turned 180 degrees about Z (its +X is the player's
        # left and its +Y points down), so the vanilla angles above come across with pitch and
        # yaw negated and roll unchanged. Keeping the published values in Minecraft's convention
        # means the formulas stay copy-comparable with BipedEntityModel.
        local = rotation_matrix(-pitch, -yaw, roll)
        parent = PARENTS[name]
        pivot = PIVOTS[name]
        if parent is None:
            parts[name] = (pivot, local, IDENTITY)
        else:
            p_pivot, p_matrix, _ = parts[parent]
            # Re-express this pivot in the parent's frame so the chain stays rigid.
            rel = tuple(pivot[i] - p_pivot[i] for i in range(3))
            moved = mat_apply(p_matrix, rel)
            world_pivot = tuple(p_pivot[i] + moved[i] for i in range(3))
            parts[name] = (world_pivot, mat_mul(p_matrix, local), IDENTITY)

    offset_y = -3.0 * sneak
    return RigPose(
        {k: (v[0], v[1]) for k, v in parts.items()},
        (0.0, offset_y, 0.0),
        bool(pose.flags & P.POSE_SLIM),
        bool(pose.flags & P.POSE_MAIN_HAND_LEFT),
    )


class ModelRenderer:
    """Rasterizes the posed model into Canvas spans, amortized over several frames.

    Borderlands 2 calls Python on its game thread, so a full rasterization in one frame would be
    a visible hitch. :meth:`step` draws a bounded number of faces per call and only swaps the
    finished image in when it is complete, which keeps the per-frame cost flat and small.
    """

    def __init__(self, height: int = 72, faces_per_step: int = 14):
        self.height = max(16, int(height))
        self.width = max(12, int(self.height * 0.78) | 1)
        self.faces_per_step = max(1, int(faces_per_step))
        self.spans: list[tuple[int, int, int, int]] = []
        self.span_size = (self.width, self.height)
        self._fb: Framebuffer | None = None
        self._raster: Rasterizer | None = None
        self._queue: list = []
        self._pending_key = None
        self._current_key = None
        self.renders_completed = 0
        self.faces_drawn = 0

    # -- sizing ---------------------------------------------------------------------------
    def resize(self, height: int) -> None:
        height = max(16, int(height))
        if height == self.height:
            return
        self.height = height
        self.width = max(12, int(height * 0.78) | 1)
        self._fb = None
        self._queue = []
        self._pending_key = None
        self._current_key = None

    # -- rendering ------------------------------------------------------------------------
    def begin(self, texture: Texture, pose: "P.PoseState", key) -> bool:
        """Queue a new rasterization. Returns False when this pose is already on screen."""
        if key == self._current_key or key == self._pending_key:
            return False
        rig = evaluate_rig(pose)
        boxes = _BOXES_SLIM if rig.slim else _BOXES_WIDE

        width, height = self.width, self.height
        if self._fb is None or self._fb.width != width or self._fb.height != height:
            self._fb = Framebuffer(width, height)
        else:
            self._fb.clear()
        focal = height * CAMERA_DISTANCE / MODEL_FRAME_UNITS
        self._raster = Rasterizer(self._fb, focal, width * 0.5, height * 0.5)

        # Minecraft yaw grows clockwise seen from above (0 = +Z/south, 90 = -X/west). With the
        # camera behind the player at a relative yaw of 0 we must see the back, and a relative
        # yaw of +90 must show the player's right-hand side; 180 - yaw is the mapping that does
        # both in this model space.
        yaw = math.radians(180.0 - pose.body_yaw)
        view = rotation_matrix(0.0, yaw, 0.0)
        tint = ((255, 70, 70), min(1.0, max(0.0, pose.hurt_time)) * 0.6) if pose.hurt_time > 0 else None

        queue = []
        for box in boxes:
            pivot, matrix = rig.parts[box.part]
            if box.overlay and not texture.opaque_region(box.uv[0], box.uv[1], 1, 1) \
                    and not texture.opaque_region(box.uv[0] + box.size[2], box.uv[1] + box.size[2],
                                                  box.size[0], box.size[1]):
                continue  # fully transparent second layer: skip the whole box
            for face_name, corners, uvs in box.faces():
                view_corners = []
                for corner in corners:
                    local = mat_apply(matrix, corner)
                    model = (local[0] + pivot[0] + rig.offset[0],
                             local[1] + pivot[1] + rig.offset[1],
                             local[2] + pivot[2] + rig.offset[2])
                    v = mat_apply(view, (model[0], model[1] - MODEL_CENTER_Y, model[2]))
                    view_corners.append((v[0], v[1], CAMERA_DISTANCE - v[2]))
                queue.append((view_corners, uvs, texture, FACE_SHADE[face_name], tint))

        item = _held_item_faces(rig, pose, view, tint)
        if item:
            queue.extend(item)

        self._queue = queue
        self._pending_key = key
        return True

    def step(self, budget: int | None = None) -> bool:
        """Draw up to ``budget`` faces. Returns True when the image became complete."""
        if not self._queue or self._raster is None:
            return False
        budget = self.faces_per_step if budget is None else max(1, int(budget))
        drawn = 0
        while self._queue and drawn < budget:
            corners, uvs, texture, shade, tint = self._queue.pop(0)
            if isinstance(texture, Texture):
                self._raster.quad(corners, uvs, texture, shade, tint)
            else:
                self._raster.solid_quad(corners, texture, shade)
            drawn += 1
        self.faces_drawn += drawn
        if self._queue:
            return False
        self.spans = self._fb.spans()
        self.span_size = (self.width, self.height)
        self._current_key = self._pending_key
        self._pending_key = None
        self.renders_completed += 1
        return True

    def render_now(self, texture: Texture, pose: "P.PoseState", key=None) -> list:
        """Rasterize a pose in one shot (tests and the first frame after a skin change)."""
        self._current_key = None
        self._pending_key = None
        self.begin(texture, pose, key if key is not None else object())
        while self._queue:
            self.step(1_000_000)
        return self.spans


def _held_item_faces(rig: RigPose, pose: "P.PoseState", view, tint):
    """A simple coloured cuboid in the main hand so carried items are visible from Pandora."""
    rgb = pose.held_main_rgb & 0xFFFFFF
    if not rgb:
        return []
    part = "left_arm" if rig.main_hand_left else "right_arm"
    pivot, matrix = rig.parts[part]
    sign = -1.0 if rig.main_hand_left else 1.0
    rgba = 0xFF000000 | rgb
    # Item box sits just past the hand: 4x4x8 units, angled like a held tool.
    hand = (sign * 0.5, -12.0, -1.0)
    size = (3.0, 3.0, 8.0)

    def to_view(point):
        local = mat_apply(matrix, point)
        model = (local[0] + pivot[0] + rig.offset[0],
                 local[1] + pivot[1] + rig.offset[1],
                 local[2] + pivot[2] + rig.offset[2])
        v = mat_apply(view, (model[0], model[1] - MODEL_CENTER_Y, model[2]))
        return (v[0], v[1], CAMERA_DISTANCE - v[2])

    x0 = hand[0] - size[0] * 0.5
    x1 = hand[0] + size[0] * 0.5
    y0 = hand[1] - size[1] * 0.5
    y1 = hand[1] + size[1] * 0.5
    z0 = hand[2] - size[2] * 0.5
    z1 = hand[2] + size[2] * 0.5
    faces = (
        ("top", [(x1, y1, z1), (x0, y1, z1), (x0, y1, z0), (x1, y1, z0)]),
        ("bottom", [(x1, y0, z0), (x0, y0, z0), (x0, y0, z1), (x1, y0, z1)]),
        ("right", [(x1, y1, z1), (x1, y1, z0), (x1, y0, z0), (x1, y0, z1)]),
        ("front", [(x1, y1, z0), (x0, y1, z0), (x0, y0, z0), (x1, y0, z0)]),
        ("left", [(x0, y1, z0), (x0, y1, z1), (x0, y0, z1), (x0, y0, z0)]),
        ("back", [(x0, y1, z1), (x1, y1, z1), (x1, y0, z1), (x0, y0, z1)]),
    )
    return [([to_view(c) for c in corners], None, rgba, FACE_SHADE[name], tint)
            for name, corners in faces]


def pose_key(pose: "P.PoseState", skin_id: int, height: int) -> tuple:
    """Quantize a pose so tiny jitters do not trigger a re-rasterization."""
    return (
        skin_id,
        height,
        int(pose.flags) & (P.POSE_SLIM | P.POSE_MAIN_HAND_LEFT),
        int(round(pose.body_yaw / 6.0)),
        int(round(_wrap(pose.head_yaw - pose.body_yaw) / 12.0)),
        int(round(pose.head_pitch / 12.0)),
        int(round(pose.limb_swing * 2.0)),
        int(round(pose.limb_swing_amount * 4.0)),
        int(round(pose.hand_swing * 4.0)),
        int(round(pose.sneak_amount * 2.0)),
        int(round(pose.lean_angle / 15.0)),
        1 if pose.held_main_rgb else 0,
        int(round(min(1.0, max(0.0, pose.hurt_time)) * 2.0)),
    )
