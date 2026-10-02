// BorderCraft shared-memory protocol — Java mirror.
//
// Mirror of protocol/bordercraft_protocol.h (the single source of truth). Keep in sync with that
// header and with protocol/python/bordercraft_protocol.py, and bump VERSION on any layout change.
package dev.bordercraft.link;

public final class Proto {
    private Proto() {}

    public static final int MAGIC = 0x54464342; // "BCFT"
    public static final int VERSION = 1;
    public static final String MAPPING_FILENAME = "bridge.mm";
    public static final String MAPPING_NAME = "Local\\BorderCraft_v1"; // Windows named-mapping upgrade
    public static final double UNITS_PER_BLOCK = 53.3333;

    // ---- region offsets ---------------------------------------------------------------------
    public static final long OFF_HEADER = 0x0;
    public static final long OFF_BL2_STATE = 0x100;
    public static final long OFF_MC_STATE = 0x200;
    public static final long OFF_OVERLAY_CTL = 0x300;
    public static final long OFF_OVERLAY_SLOT_HDR = 0x340;
    public static final long OFF_WATER_GRID = 0x400;
    public static final long OFF_INPUT_RING = 0x1000;
    public static final long OFF_COLLISION_RING = 0x12000;
    public static final long OFF_ACTOR_TABLE = 0x453000;
    public static final long OFF_EVENT_RING = 0x457000;
    public static final long OFF_OVERLAY_PIXELS = 0x480000;

    public static final int MAX_OVERLAY_W = 1920;
    public static final int MAX_OVERLAY_H = 1080;
    public static final long OVERLAY_SLOT_BYTES = (long) MAX_OVERLAY_W * MAX_OVERLAY_H * 4;
    public static final int OVERLAY_SLOTS = 2;
    public static final long MAPPING_BYTES = OFF_OVERLAY_PIXELS + OVERLAY_SLOT_BYTES * OVERLAY_SLOTS;

    public static final int INPUT_RING_ENTRIES = 4096;
    public static final int COLLISION_RING_ENTRIES = 4096;
    public static final int COLLISION_REC_BYTES = 0x440;
    public static final int MAX_ACTORS = 256;
    public static final int EVENT_RING_ENTRIES = 4096;
    public static final long RING_BASE = 0x80; // head @+0x00, tail @+0x40, records @+0x80

    // ---- struct sizes ------------------------------------------------------------------------
    public static final int HEADER_BYTES = 0x20;
    public static final int BL2_STATE_BYTES = 0x40;
    public static final int MC_STATE_BYTES = 0xC8;
    public static final int INPUT_ENTRY_BYTES = 0x10;
    public static final int EVENT_ENTRY_BYTES = 0x20;
    public static final int ACTOR_ENTRY_BYTES = 0x30;
    public static final int ACTOR_TABLE_HEAD_BYTES = 0x40;

    // ---- flags ---------------------------------------------------------------------------------
    public static final int BL2_IN_GAME = 1 << 0;
    public static final int BL2_MENU_OPEN = 1 << 1;
    public static final int BL2_LOADING = 1 << 2;
    public static final int BL2_PAUSED = 1 << 3;

    public static final int MC_IN_WORLD = 1 << 0;
    public static final int MC_SCREEN_OPEN = 1 << 1;
    public static final int MC_ON_GROUND = 1 << 2;
    public static final int MC_SNEAKING = 1 << 3;
    public static final int MC_SPRINTING = 1 << 4;
    public static final int MC_DEAD = 1 << 5;
    public static final int MC_SWIMMING = 1 << 6;
    public static final int MC_FLYING = 1 << 7;

    // OverlayCtl.state flag.
    public static final int OVERLAY_DIRTY = 1 << 2;
    // Overlay slot payload flags (a separate field from the control word above).
    public static final int OVERLAY_BOTTOM_UP = 0x1;
    public static final int OVERLAY_SKIN = 0x2;
    public static final int OVERLAY_SLIM = 0x4;

    public static final int COL_WALKABLE = 1 << 0;
    public static final int COL_WATER = 1 << 1;
    public static final int COL_STEEP = 1 << 2;
    public static final int COL_HEIGHTFIELD = 0;
    public static final int COL_AABBS = 1;

    public static final int ACTOR_HOSTILE = 1 << 0;
    public static final int ACTOR_DEAD = 1 << 1;
    public static final int ACTOR_BOSS = 1 << 2;
    public static final int ACTOR_TARGETED = 1 << 3;
    public static final int ACTOR_IN_COMBAT = 1 << 4;

    // input types
    public static final int IN_KEY_DOWN = 1, IN_KEY_UP = 2, IN_MOUSE_MOVE = 3;
    public static final int IN_MOUSE_DOWN = 4, IN_MOUSE_UP = 5, IN_MOUSE_WHEEL = 6, IN_FOCUS_LOST = 7;

    // event types
    public static final int EVT_PLAYER_HIT_ACTOR = 1, EVT_ACTOR_HIT_PLAYER = 2;
    public static final int EVT_BLOCK_PLACE = 3, EVT_BLOCK_BREAK = 4;
    public static final int EVT_PLAYER_DIED = 5, EVT_PLAYER_RESPAWNED = 6;
    public static final int EVT_SOUND_PLAY = 7, EVT_APPROACH_ACTOR = 8;

    public static final float NO_WATER = -1.0e30f;

    // McState field offsets (for direct buffer access)
    public static final long MC_OFF_FLAGS = 0x04, MC_OFF_X = 0x08, MC_OFF_Y = 0x10, MC_OFF_Z = 0x18;
    public static final long MC_OFF_YAW = 0x20, MC_OFF_PITCH = 0x24, MC_OFF_TELEPORT_ACK = 0x30;
    public static final long MC_OFF_FRAME_COUNTER = 0x38, MC_OFF_FOV = 0x40;
    public static final long MC_OFF_EYE_X = 0x50, MC_OFF_EYE_Y = 0x58, MC_OFF_EYE_Z = 0x60;

    // Bl2State field offsets
    public static final long BL2_OFF_FLAGS = 0x04, BL2_OFF_WORLD_ID = 0x08, BL2_OFF_EPOCH = 0x0C;
    public static final long BL2_OFF_POS_X = 0x10, BL2_OFF_POS_Y = 0x18, BL2_OFF_POS_Z = 0x20;
    public static final long BL2_OFF_YAW = 0x28, BL2_OFF_PITCH = 0x2C, BL2_OFF_TELEPORT_SEQ = 0x30;

    /** Unreal (Z-up) -> Minecraft (Y-up). Signs pinned exactly in Phase 0 calibration. */
    public static double[] ueToMc(double x, double y, double z) {
        return new double[] { x / UNITS_PER_BLOCK, z / UNITS_PER_BLOCK, y / UNITS_PER_BLOCK };
    }

    /** Minecraft (Y-up) -> Unreal (Z-up). */
    public static double[] mcToUe(double x, double y, double z) {
        return new double[] { x * UNITS_PER_BLOCK, z * UNITS_PER_BLOCK, y * UNITS_PER_BLOCK };
    }
}
