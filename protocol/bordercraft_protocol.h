// BorderCraft shared-memory protocol (Borderlands 2 Python SDK mod <-> Minecraft Fabric mod).
//
// This header is the single source of truth for the byte layout. Mirrors:
//   - Python: protocol/python/bordercraft_protocol.py  (BL2 side + tools)
//   - Java:   fabric/src/main/java/dev/bordercraft/link/Proto.java
// If you change anything here, change the mirrors too and bump kVersion.
//
// All multi-byte values are little-endian. BL2 creates the mapping; Minecraft opens it.
// Coordinates in this protocol are always Minecraft space (blocks, Y up, Z south) unless noted.
// (Modeled on chasmlol/SkyCraft's skycraft_protocol.h — same ideas, BL2 flavor.)
#pragma once

#include <cstdint>

namespace bordercraft::proto
{
	inline constexpr std::uint32_t kMagic = 0x54464342;  // "BCFT"
	// v2 appends the gameplay regions (player pose, HUD/inventory, placed-block mirror) after
	// the overlay pixels. Every v1 offset is unchanged, so only the new regions are additive.
	inline constexpr std::uint32_t kVersion = 2;

	// Named-mapping upgrade path (Windows). v1 transport is a file-backed mapping both sides
	// open at the same path (see docs/DESIGN.md, "Transport").
	inline constexpr wchar_t kMappingName[] = L"Local\\BorderCraft_v1";
	inline constexpr char  kMappingFileName[] = "bridge.mm";

	// 1 Minecraft block == kUnitsPerBlock Unreal units. BL2 pawns are ~96 uu tall and the
	// Minecraft player is 1.8 blocks, so 53.3333 uu per block. Pinned precisely in Phase 0.
	inline constexpr double kUnitsPerBlock = 53.3333;

	// ---- region offsets ---------------------------------------------------------------------
	inline constexpr std::uint64_t kOffHeader = 0x0;
	inline constexpr std::uint64_t kOffBl2State = 0x100;      // BL2 -> MC, seqlock
	inline constexpr std::uint64_t kOffMcState = 0x200;       // MC -> BL2, seqlock
	inline constexpr std::uint64_t kOffOverlayCtl = 0x300;    // frame triple-buffer control
	inline constexpr std::uint64_t kOffOverlaySlotHdr = 0x340;  // 2 x 0x40
	inline constexpr std::uint64_t kOffWaterGrid = 0x400;     // BL2 -> MC, seqlock
	inline constexpr std::uint64_t kOffInputRing = 0x1000;    // BL2 -> MC, see InputEntry
	inline constexpr std::uint64_t kOffCollisionRing = 0x12000; // BL2 -> MC, see CollisionRec
	inline constexpr std::uint64_t kOffActorTable = 0x453000;   // BL2 -> MC, see ActorTable
	inline constexpr std::uint64_t kOffEventRing = 0x457000;    // MC -> BL2, see McEvent
	// Overlay pixel slots: 2 x 1920x1080 BGRA.
	inline constexpr std::uint64_t kOffOverlayPixels = 0x480000;
	inline constexpr std::uint32_t kMaxOverlayW = 1920;
	inline constexpr std::uint32_t kMaxOverlayH = 1080;
	inline constexpr std::uint64_t kOverlaySlotBytes = std::uint64_t(kMaxOverlayW) * kMaxOverlayH * 4;
	inline constexpr std::uint32_t kOverlaySlots = 2;
	// v2 gameplay regions, appended after the overlay pixels (0x1452000).
	inline constexpr std::uint64_t kOffPose = 0x1452000;    // MC -> BL2, seqlock, 0x100 bytes
	inline constexpr std::uint64_t kOffHud = 0x1452100;     // MC -> BL2, seqlock, HudState
	inline constexpr std::uint64_t kOffBlocks = 0x1453000;  // MC -> BL2, seqlock, BlockTable
	// BL2 -> MC events (the event ring at kOffEventRing only runs MC -> BL2). This is how a
	// bandit's bullet becomes real damage on the Minecraft player.
	inline constexpr std::uint64_t kOffBl2EventRing = 0x1460000;
	inline constexpr std::uint32_t kBl2EventRingEntries = 4096;
	inline constexpr std::uint64_t kMappingBytes = 0x1490000;

	// ---- header @0x0 ------------------------------------------------------------------------
	struct Header
	{
		std::uint32_t magic;
		std::uint32_t version;
		std::uint32_t bl2Pid;
		std::uint32_t mcPid;
		std::uint64_t bl2HeartbeatMs;  // monotonic ms at last BL2 frame
		std::uint64_t mcHeartbeatMs;   // monotonic ms at last MC frame
	};
	static_assert(sizeof(Header) == 0x20);

	// ---- BL2 -> MC state @0x100 (seqlock: seq odd while writing) ----------------------------
	enum Bl2Flags : std::uint32_t
	{
		kBl2InGame = 1u << 0,    // a save is loaded and the player pawn exists
		kBl2MenuOpen = 1u << 1,  // a BL2 menu owns input; MC should drop held keys
		kBl2Loading = 1u << 2,   // loading screen / map transition in progress
		kBl2Paused = 1u << 3,
	};

	struct Bl2State
	{
		std::uint32_t seq;
		std::uint32_t flags;           // Bl2Flags
		std::uint32_t worldId;         // CRC32 of the current BL2 map name
		std::uint32_t collisionEpoch;  // bumps on map change; MC drops all collision data
		double        posX, posY, posZ;  // BL2 player feet, MC coords
		float         yaw, pitch;        // authoritative look (MC degrees)
		std::uint32_t teleportSeq;       // MC teleports its player to pos when this changes
		std::uint32_t viewportW;
		std::uint32_t viewportH;
		float         gameSpeed;         // BL2 TimeDilation (1 = normal); MC may match it
	};
	static_assert(sizeof(Bl2State) == 0x40);

	// ---- MC -> BL2 state @0x200 (seqlock) --------------------------------------------------
	enum McFlags : std::uint32_t
	{
		kMcInWorld = 1u << 0,
		kMcScreenOpen = 1u << 1,  // an MC GUI screen (inventory, chat, ...) is open
		kMcOnGround = 1u << 2,
		kMcSneaking = 1u << 3,
		kMcSprinting = 1u << 4,
		kMcDead = 1u << 5,
		kMcSwimming = 1u << 6,
		kMcFlying = 1u << 7,
	};

	struct McState
	{
		std::uint32_t seq;
		std::uint32_t flags;          // McFlags
		double        x, y, z;        // interpolated feet position (MC coords)
		float         yaw, pitch;     // MC rotation (degrees)
		float         eyeHeight;      // blocks above feet
		float         sensitivity;    // MC mouse sensitivity option (0..1)
		std::uint32_t teleportAck;    // last Bl2State::teleportSeq applied
		std::uint32_t guiScale;
		std::uint64_t frameCounter;
		float         fovDeg;         // effective vertical FOV (includes sprint modifiers)
		float         bobPhase;       // MC walk-bob phase (interpolated walk distance)
		float         bobAmount;      // MC walk-bob amplitude
		std::uint32_t pad4C;
		double        eyeX, eyeY, eyeZ;  // MC camera position (interpolated)

		// Raw 20 Hz physics ticks, so BL2 can interpolate on its own frame clock exactly like
		// Minecraft's renderer does with partial ticks (no judder between the two frame clocks).
		std::int64_t tickQpc;
		double       prevX, prevY, prevZ;  // feet at the previous tick
		double       curX, curY, curZ;     // feet at the latest tick
		float        tickEyeO, tickEye;    // smoothed eye height, previous/latest tick
		float        walkDistO, walkDist;  // walk-bob phase inputs
		float        bobO, bob;            // walk-bob amplitude inputs
		float        tickMs;               // ms per tick (50 unless tick rate changed)
		std::uint32_t tickPad;

		// Minecraft's camera (F5): 0 first person, 1 third person behind, 2 third person in front.
		std::uint32_t cameraMode;
		float         cameraDistance;
	};
	static_assert(sizeof(McState) == 0xC8);
	static_assert(sizeof(McState) <= 0x100);

	// ---- overlay double buffer @0x300 -------------------------------------------------------
	// Same scheme as SkyCraft: bits 0-1 = index of the "middle" slot, bit 2 = middle holds an
	// unread frame. Writer (MC) renders into its private back slot, then
	// xchg(state, back | kDirty) and keeps the returned index as its new back slot. Reader (BL2)
	// does xchg(state, front) only when the dirty bit is set and keeps the returned index as its
	// new front slot.
	// OverlayCtl::state flag (this lives in the control word, not OverlaySlotHdr::flags).
	inline constexpr std::uint32_t kOverlayDirty = 1u << 2;

	// OverlaySlotHdr::flags. Phase 1a sends the authenticated player's skin rather than a
	// fullscreen readback: the BL2 Canvas renderer can display this without a native D3D9 upload.
	inline constexpr std::uint32_t kOverlayBottomUp = 1u << 0;
	inline constexpr std::uint32_t kOverlaySkin = 1u << 1;
	inline constexpr std::uint32_t kOverlaySlim = 1u << 2;

	struct OverlayCtl
	{
		std::uint32_t state;
		std::uint32_t pad;
		std::uint64_t framesPublished;
	};

	struct OverlaySlotHdr
	{
		std::uint32_t width;
		std::uint32_t height;
		std::uint32_t flags;  // OverlaySlotFlags: bottom-up rows / skin payload / slim arms
		std::uint32_t pad;
		std::uint64_t frameId;
		std::uint8_t  reserved[0x40 - 0x18];
	};
	static_assert(sizeof(OverlaySlotHdr) == 0x40);

	// ---- BL2 water around the player @0x400 (seqlock) --------------------------------------
	inline constexpr std::uint32_t kWaterGridSize = 16;
	inline constexpr float         kNoWater = -1.0e30f;

	struct WaterGrid
	{
		std::uint32_t seq;
		std::int32_t  originX, originZ;  // MC block column of surface[0]
		std::uint32_t worldId;           // as in Bl2State
		float         surface[kWaterGridSize * kWaterGridSize];  // MC y of surface; kNoWater: none
	};
	static_assert(sizeof(WaterGrid) <= 0xC00);

	// ---- input ring @0x1000 (BL2 produces, MC consumes) ------------------------------------
	inline constexpr std::uint32_t kInputRingEntries = 4096;  // power of two
	inline constexpr std::uint64_t kInputRingHeadOff = 0x00;  // u64, written by BL2
	inline constexpr std::uint64_t kInputRingTailOff = 0x40;  // u64, written by MC
	inline constexpr std::uint64_t kInputRingBaseOff = 0x80;

	enum InputType : std::uint16_t
	{
		kKeyDown = 1,
		kKeyUp = 2,
		kMouseMove = 3,   // value = dx, aux = dy (raw counts)
		kMouseDown = 4,   // code = button
		kMouseUp = 5,
		kMouseWheel = 6,  // value = notches
		kFocusLost = 7,   // MC should release all held keys
	};

	// `code` is a GLFW key code (kKeyDown/kKeyUp) or a GLFW mouse button (kMouseDown/kMouseUp),
	// because that is the namespace Minecraft's own key bindings live in: the Fabric side can
	// hand the event straight to KeyBinding without a second translation table.
	struct InputEntry  // 16 bytes
	{
		std::uint16_t type;     // InputType
		std::uint16_t code;     // GLFW key code / mouse button
		std::int32_t  value;
		std::int32_t  aux;
		std::uint32_t timeMs;   // low 32 bits of producer's monotonic ms
	};
	static_assert(sizeof(InputEntry) == 0x10);

	// ---- collision ring @0x12000 (BL2 produces, MC consumes) -------------------------------
	// Voxelized BL2 collision around the player, streamed as per-section records at
	// 1/8-block resolution. These are NOT blocks: MC injects them as extra collision shapes
	// (CollisionField), so player-placed blocks are free to sit next to BL2 geometry.
	inline constexpr std::uint32_t kCollisionRingEntries = 4096;
	inline constexpr std::uint64_t kCollisionRingHeadOff = 0x00;  // u64, written by BL2
	inline constexpr std::uint64_t kCollisionRingTailOff = 0x40;  // u64, written by MC
	inline constexpr std::uint64_t kCollisionRingBaseOff = 0x80;

	enum CollisionKind : std::uint8_t
	{
		kColHeightfield = 0,  // payload: 256 columns (16x16, x + z*16) of {i16 height, u8 flags, u8 pad}
		kColAabbs = 1,        // payload: count * {i16 x,y,z, sx,sy,sz, u16 flags, u16 pad}
	};

	enum ColFlags : std::uint8_t
	{
		kColWalkable = 1u << 0,
		kColWater = 1u << 1,
		kColSteep = 1u << 2,  // surface steeper than the walkable slope; MC treats as a wall
	};

	struct CollisionRec  // 0x440 bytes
	{
		std::uint32_t seq;
		std::uint32_t epoch;     // must match Bl2State::collisionEpoch
		std::int16_t  secX, secY, secZ;  // section = 16x16x16 blocks
		std::uint8_t  kind;      // CollisionKind
		std::uint8_t  count;     // kColAabbs only (<= 64)
		std::uint16_t pad;
		std::uint32_t flags;
		std::uint8_t  reserved[0x40 - 0x16];
		std::uint8_t  payload[0x400];
	};
	static_assert(sizeof(CollisionRec) == 0x440);

	// ---- actor table @0x453000 (BL2 -> MC, seqlock) ----------------------------------------
	// Nearby BL2 pawns (bandits, wildlife, bots) so MC can proxy them as hittable entities.
	inline constexpr std::uint32_t kMaxActors = 256;

	enum ActorFlags : std::uint32_t
	{
		kActorHostile = 1u << 0,
		kActorDead = 1u << 1,
		kActorBoss = 1u << 2,
		kActorTargeted = 1u << 3,
		kActorInCombat = 1u << 4,
	};

	struct ActorEntry  // 0x30 bytes
	{
		std::uint32_t id;        // stable handle while the pawn lives
		std::uint32_t flags;     // ActorFlags
		float         x, y, z;   // feet, MC coords
		float         yaw;       // degrees
		float         health;
		float         maxHealth;
		float         halfW;     // proxy box half-width, blocks
		float         height;    // proxy box height, blocks
		std::uint64_t extra;     // BL2-defined (e.g. weapon in hand)
	};
	static_assert(sizeof(ActorEntry) == 0x30);

	struct ActorTable
	{
		std::uint32_t seq;
		std::uint32_t count;
		std::uint8_t  reserved[0x40 - 0x8];
		ActorEntry    entries[kMaxActors];
	};

	// ---- event ring @0x457000 (MC produces, BL2 consumes) ----------------------------------
	inline constexpr std::uint32_t kEventRingEntries = 4096;
	inline constexpr std::uint64_t kEventRingHeadOff = 0x00;
	inline constexpr std::uint64_t kEventRingTailOff = 0x40;
	inline constexpr std::uint64_t kEventRingBaseOff = 0x80;

	enum McEventType : std::uint16_t
	{
		kEvtPlayerHitActor = 1,   // actorId, a=damage, b=critical(0/1), c=knockback, x/y/z=hit point
		kEvtActorHitPlayer = 2,   // actorId, a=damage
		kEvtBlockPlace = 3,       // x/y/z=block pos (ints in float), flags=block id
		kEvtBlockBreak = 4,
		kEvtPlayerDied = 5,
		kEvtPlayerRespawned = 6,
		kEvtSoundPlay = 7,        // flags=sound id, x/y/z=pos, a=volume
		kEvtApproachActor = 8,    // BL2 "activate" pressed near actorId (talk/loot/press button)
	};

	struct McEvent  // 0x20 bytes
	{
		std::uint16_t type;     // McEventType
		std::uint16_t flags;
		std::uint32_t actorId;
		float         a, b, c;
		float         x, y, z;
	};
	static_assert(sizeof(McEvent) == 0x20);

	// ---- player pose @0x1452000 (MC -> BL2, seqlock) ---------------------------------------
	// Everything BL2 needs to pose the real 3D Minecraft player model: Minecraft computes the
	// animation inputs (it owns the physics), BL2 only evaluates the vanilla biped rig from them.
	enum PoseFlags : std::uint32_t
	{
		kPoseSlim = 1u << 0,      // 3px arms (Alex model)
		kPoseSneaking = 1u << 1,
		kPoseSwimming = 1u << 2,
		kPoseSprinting = 1u << 3,
		kPoseUsingItem = 1u << 4,
		kPoseMainHandLeft = 1u << 5,
		kPoseOnGround = 1u << 6,
		kPoseInvisible = 1u << 7,
	};

	struct PoseState
	{
		std::uint32_t seq;
		std::uint32_t flags;        // PoseFlags
		float bodyYaw;              // degrees, interpolated
		float headYaw;              // degrees, absolute (head.yaw = headYaw - bodyYaw)
		float headPitch;            // degrees, positive looks down (Minecraft convention)
		float limbSwing;            // LimbAnimator position (drives the biped walk cycle)
		float limbSwingAmount;      // 0..1 walk amplitude
		float handSwing;            // 0..1 main-hand swing progress
		float sneakAmount;          // 0..1 crouch blend
		float leanAngle;            // degrees of forward lean (swimming / elytra / crawling)
		float scale;                // model scale multiplier (1.0 normally)
		float velX, velY, velZ;     // blocks per tick, for secondary motion on the BL2 side
		std::uint32_t heldMainRgb;  // 0x00RRGGBB of the main-hand item (0 = empty)
		std::uint32_t heldOffRgb;
		float fallDistance;
		float hurtTime;             // 0..1 red damage tint
		std::uint32_t reserved[8];
	};
	static_assert(sizeof(PoseState) <= 0x100);

	// ---- HUD + inventory @0x1452100 (MC -> BL2, seqlock) -----------------------------------
	// Minecraft owns the inventory; this is a read-only projection so BL2 can draw the real
	// Minecraft HUD (hearts, armor, hunger, XP, hotbar) and inventory grid over Pandora.
	inline constexpr std::uint32_t kHudSlots = 46;       // 0-8 hotbar, 9-35 main, 36-39 armor, 40 offhand
	inline constexpr std::uint32_t kHudNameBytes = 24;   // UTF-8, NUL padded

	enum HudFlags : std::uint32_t
	{
		kHudScreenOpen = 1u << 0,     // an MC GUI (inventory/chest/crafting) is open
		kHudInventoryOpen = 1u << 1,  // that screen is the player inventory
		kHudCreative = 1u << 2,
		kHudHardcore = 1u << 3,
		kHudUnderwater = 1u << 4,
	};

	enum SlotFlags : std::uint16_t
	{
		kSlotSelected = 1u << 0,
		kSlotEnchanted = 1u << 1,
		kSlotBlock = 1u << 2,   // item places a block; rgb is its Minecraft map color
		kSlotDamaged = 1u << 3,
	};

	struct HudSlot  // 0x20 bytes
	{
		std::uint32_t itemHash;   // stable hash of the registry id (0 = empty)
		std::uint16_t count;
		std::uint16_t flags;      // SlotFlags
		std::uint32_t rgb;        // 0x00RRGGBB swatch color
		std::uint16_t damage;     // 0..1000 fraction of durability used
		std::uint16_t pad;
		char          name[kHudNameBytes];
	};
	static_assert(sizeof(HudSlot) == 0x28);

	struct HudState
	{
		std::uint32_t seq;
		std::uint32_t flags;        // HudFlags
		float health, maxHealth, absorption;
		float armor, food, saturation, air, maxAir;
		std::uint32_t xpLevel;
		float         xpProgress;   // 0..1
		std::uint32_t selectedSlot; // 0..8
		std::uint32_t slotCount;    // <= kHudSlots
		std::uint32_t reserved[2];  // pads the header to exactly 0x40 bytes
		HudSlot       slots[kHudSlots];
	};
	static_assert(sizeof(HudState) == 0x40 + 0x28 * kHudSlots);
	static_assert(sizeof(HudState) <= 0xF00);

	// ---- placed blocks @0x1453000 (MC -> BL2, seqlock) -------------------------------------
	// Blocks the player placed in the mirror world near the camera, so BL2 can draw them as
	// real 3D cubes in Pandora. Minecraft stays authoritative: this is a render-only mirror.
	inline constexpr std::uint32_t kMaxBlocks = 2048;

	enum BlockEntryFlags : std::uint8_t
	{
		kBlockTranslucent = 1u << 0,
		kBlockEmissive = 1u << 1,
		kBlockFullCube = 1u << 2,
	};

	struct BlockEntry  // 0x10 bytes
	{
		std::int32_t  x, y, z;   // block position, MC coords
		std::uint8_t  r, g, b;   // Minecraft map color
		std::uint8_t  flags;     // BlockEntryFlags
	};
	static_assert(sizeof(BlockEntry) == 0x10);

	struct BlockTable
	{
		std::uint32_t seq;
		std::uint32_t count;
		std::uint32_t worldId;
		std::uint32_t revision;  // bumps whenever the set changes
		std::uint8_t  reserved[0x40 - 0x10];
		BlockEntry    entries[kMaxBlocks];
	};
	static_assert(sizeof(BlockTable) == 0x40 + 0x10 * kMaxBlocks);
}
