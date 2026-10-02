// BorderCraft - Minecraft as the authority on where the player is.
//
// While Minecraft physics is engaged, Borderlands 2 stops simulating its pawn and follows the
// Minecraft player instead. This class owns both halves of that contract: it feeds Pandora's
// collision into the Minecraft solver (via CollisionField), and it publishes the full McState -
// position, look, eye height, view bob, the interpolation endpoints BL2 needs to drive a smooth
// camera at its own frame rate - once per client tick.
package dev.bordercraft.bridge;

import dev.bordercraft.link.Proto;
import dev.bordercraft.link.SharedMemory;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.util.math.MathHelper;
import net.minecraft.util.math.Vec3d;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;

public final class PhysicsBridge {
    private final SharedMemory sm;
    private final ByteBuffer collisionScratch =
            ByteBuffer.allocate(Proto.COLLISION_REC_BYTES).order(ByteOrder.LITTLE_ENDIAN);

    private int lastTeleportSeq = -1;
    private int teleportAck = 0;
    private int epoch = -1;
    public int collisionRecordsApplied = 0;

    // Interpolation endpoints: BL2 renders at its own frame rate between two Minecraft ticks.
    private double prevX, prevY, prevZ;
    private float prevEye, prevWalkDistance, prevBob;

    public PhysicsBridge(SharedMemory sm) {
        this.sm = sm;
    }

    /** Apply BL2 -> MC state: teleports and map changes. Returns the Bl2State flags, or 0. */
    public int consumeBl2State(MinecraftClient client, ClientPlayerEntity player) {
        SharedMemory.Seqlock lock = sm.bl2StateLock();
        int seq = lock.tryRead();
        if (seq < 0) {
            return 0;
        }
        int base = (int) Proto.OFF_BL2_STATE;
        // Copy everything before validating: BL2 may start a new write at any moment.
        int flags = sm.buf.getInt(base + (int) Proto.BL2_OFF_FLAGS);
        int collisionEpoch = sm.buf.getInt(base + (int) Proto.BL2_OFF_EPOCH);
        double x = sm.buf.getDouble(base + (int) Proto.BL2_OFF_POS_X);
        double y = sm.buf.getDouble(base + (int) Proto.BL2_OFF_POS_Y);
        double z = sm.buf.getDouble(base + (int) Proto.BL2_OFF_POS_Z);
        float yaw = sm.buf.getFloat(base + (int) Proto.BL2_OFF_YAW);
        float pitch = sm.buf.getFloat(base + (int) Proto.BL2_OFF_PITCH);
        int teleportSeq = sm.buf.getInt(base + (int) Proto.BL2_OFF_TELEPORT_SEQ);
        if (!lock.readOk(seq)) {
            return 0;
        }

        if (collisionEpoch != epoch) {
            // A new Borderlands 2 map: every piece of collision Minecraft knows about is gone.
            epoch = collisionEpoch;
            CollisionField.INSTANCE.clear(collisionEpoch);
        }
        if (teleportSeq != lastTeleportSeq) {
            if (lastTeleportSeq != -1 && player != null) {
                // Fast travel, a story teleport, a New-U respawn: move the player to match.
                player.refreshPositionAndAngles(x, y, z, yaw, pitch);
                player.setVelocity(Vec3d.ZERO);
                player.fallDistance = 0.0f;
            }
            lastTeleportSeq = teleportSeq;
            teleportAck = teleportSeq;
        }
        return flags;
    }

    /** Decode the collision ring into the field the mixin consults. */
    public void drainCollision(ClientPlayerEntity player) {
        SharedMemory.Ring ring = sm.collisionRing();
        int applied = 0;
        while (ring.pop(collisionScratch)) {
            if (CollisionField.INSTANCE.accept(collisionScratch, epoch)) {
                applied++;
            }
        }
        collisionRecordsApplied += applied;
        if (applied > 0 && player != null) {
            CollisionField.INSTANCE.evictFarSections(player.getX(), player.getZ(), 6);
        }
    }

    public void publishMcState(MinecraftClient client, ClientPlayerEntity player, long frames) {
        int flags = Proto.MC_IN_WORLD;
        if (client.currentScreen != null) flags |= Proto.MC_SCREEN_OPEN;
        if (player.isOnGround()) flags |= Proto.MC_ON_GROUND;
        if (player.isSneaking()) flags |= Proto.MC_SNEAKING;
        if (player.isSprinting()) flags |= Proto.MC_SPRINTING;
        if (player.isDead() || player.getHealth() <= 0.0f) flags |= Proto.MC_DEAD;
        if (player.isSwimming() || player.isSubmergedInWater()) flags |= Proto.MC_SWIMMING;
        if (player.getAbilities().flying) flags |= Proto.MC_FLYING;

        float eye = player.getEyeHeight(player.getPose());
        float bobPhase = player.horizontalSpeed;
        float bobAmount = computeBobAmount(player);
        double sensitivity = client.options.getMouseSensitivity().getValue();
        int guiScale = client.getWindow().getScaleFactor() > 0
                ? (int) Math.round(client.getWindow().getScaleFactor()) : 2;

        SharedMemory.Seqlock lock = sm.mcStateLock();
        int seq = lock.beginWrite();
        int off = (int) Proto.OFF_MC_STATE;
        sm.buf.putInt(off, seq);
        sm.buf.putInt(off + (int) Proto.MC_OFF_FLAGS, flags);
        sm.buf.putDouble(off + (int) Proto.MC_OFF_X, player.getX());
        sm.buf.putDouble(off + (int) Proto.MC_OFF_Y, player.getY());
        sm.buf.putDouble(off + (int) Proto.MC_OFF_Z, player.getZ());
        sm.buf.putFloat(off + (int) Proto.MC_OFF_YAW, MathHelper.wrapDegrees(player.getYaw()));
        sm.buf.putFloat(off + (int) Proto.MC_OFF_PITCH, player.getPitch());
        sm.buf.putFloat(off + (int) Proto.MC_OFF_EYE_HEIGHT, eye);
        sm.buf.putFloat(off + (int) Proto.MC_OFF_SENSITIVITY, (float) sensitivity);
        sm.buf.putInt(off + (int) Proto.MC_OFF_TELEPORT_ACK, teleportAck);
        sm.buf.putInt(off + (int) Proto.MC_OFF_GUI_SCALE, guiScale);
        sm.buf.putLong(off + (int) Proto.MC_OFF_FRAME_COUNTER, frames);
        sm.buf.putFloat(off + (int) Proto.MC_OFF_FOV, client.options.getFov().getValue().floatValue());
        sm.buf.putFloat(off + (int) Proto.MC_OFF_BOB_PHASE, bobPhase);
        sm.buf.putFloat(off + (int) Proto.MC_OFF_BOB_AMOUNT, bobAmount);
        sm.buf.putDouble(off + (int) Proto.MC_OFF_EYE_X, player.getX());
        sm.buf.putDouble(off + (int) Proto.MC_OFF_EYE_Y, player.getY() + eye);
        sm.buf.putDouble(off + (int) Proto.MC_OFF_EYE_Z, player.getZ());
        sm.buf.putLong(off + (int) Proto.MC_OFF_TICK_QPC, System.nanoTime());
        // Tick endpoints: BL2 interpolates between prev and cur so its camera is smooth even
        // though Minecraft only updates it 20 times a second.
        sm.buf.putDouble(off + (int) Proto.MC_OFF_PREV_X, prevX);
        sm.buf.putDouble(off + (int) Proto.MC_OFF_PREV_X + 8, prevY);
        sm.buf.putDouble(off + (int) Proto.MC_OFF_PREV_X + 16, prevZ);
        sm.buf.putDouble(off + (int) Proto.MC_OFF_CUR_X, player.getX());
        sm.buf.putDouble(off + (int) Proto.MC_OFF_CUR_X + 8, player.getY());
        sm.buf.putDouble(off + (int) Proto.MC_OFF_CUR_X + 16, player.getZ());
        sm.buf.putFloat(off + (int) Proto.MC_OFF_TICK_EYE_O, prevEye);
        sm.buf.putFloat(off + (int) Proto.MC_OFF_TICK_EYE_O + 4, eye);
        sm.buf.putFloat(off + (int) Proto.MC_OFF_WALK_DIST_O, prevWalkDistance);
        sm.buf.putFloat(off + (int) Proto.MC_OFF_WALK_DIST_O + 4, player.horizontalSpeed);
        sm.buf.putFloat(off + (int) Proto.MC_OFF_BOB_O, prevBob);
        sm.buf.putFloat(off + (int) Proto.MC_OFF_BOB_O + 4, bobAmount);
        sm.buf.putFloat(off + (int) Proto.MC_OFF_TICK_MS, 50.0f);
        sm.buf.putInt(off + (int) Proto.MC_OFF_CAMERA_MODE,
                client.options.getPerspective().isFirstPerson() ? 0 : 1);
        sm.buf.putFloat(off + (int) Proto.MC_OFF_CAMERA_DISTANCE, 4.0f);
        lock.endWrite(seq);

        prevX = player.getX();
        prevY = player.getY();
        prevZ = player.getZ();
        prevEye = eye;
        prevWalkDistance = player.horizontalSpeed;
        prevBob = bobAmount;
    }

    /** Vanilla's view bob amount: the same quantity GameRenderer.bobView uses. */
    private static float computeBobAmount(ClientPlayerEntity player) {
        float delta = player.horizontalSpeed - player.prevHorizontalSpeed;
        return MathHelper.clamp(Math.abs(delta) * 4.0f, 0.0f, 1.0f);
    }
}
