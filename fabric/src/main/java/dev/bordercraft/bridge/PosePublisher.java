// BorderCraft - publishes everything the Borderlands 2 side needs to draw the player model.
//
// The avatar standing in Pandora is not a sprite and not a paper doll: it is the real Minecraft
// player model, posed by Minecraft's own animation state and rendered by a software rasterizer
// on the BL2 side (bl2sdk/BorderCraft/model3d.py). This class ships that animation state - limb
// swing, head yaw, hand swing, sneak amount - once per client tick.
package dev.bordercraft.bridge;

import dev.bordercraft.link.Proto;
import dev.bordercraft.link.SharedMemory;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.client.util.SkinTextures;
import net.minecraft.util.Arm;
import net.minecraft.util.math.MathHelper;
import net.minecraft.util.math.Vec3d;

public final class PosePublisher {
    private final SharedMemory sm;

    public PosePublisher(SharedMemory sm) {
        this.sm = sm;
    }

    public void publish(MinecraftClient client, ClientPlayerEntity player) {
        int flags = 0;
        try {
            SkinTextures skin = player.getSkinTextures();
            if (skin.model() == SkinTextures.Model.SLIM) {
                flags |= Proto.POSE_SLIM;
            }
        } catch (Throwable ignored) {
            // Skin not resolved yet: the default wide model is the right assumption.
        }
        if (player.isSneaking()) flags |= Proto.POSE_SNEAKING;
        if (player.isSwimming() || player.isTouchingWater()) flags |= Proto.POSE_SWIMMING;
        if (player.isSprinting()) flags |= Proto.POSE_SPRINTING;
        if (player.isUsingItem()) flags |= Proto.POSE_USING_ITEM;
        if (player.getMainArm() == Arm.LEFT) flags |= Proto.POSE_MAIN_HAND_LEFT;
        if (player.isOnGround()) flags |= Proto.POSE_ON_GROUND;
        if (player.isInvisible()) flags |= Proto.POSE_INVISIBLE;

        // Published at the end of a client tick, so the interpolation factor is exactly 1.
        final float tickDelta = 1.0f;
        // limbAnimator is the exact value vanilla feeds to BipedEntityModel.setAngles, so the
        // walk cycle in Pandora is frame-for-frame the walk cycle Minecraft would draw.
        float limbSwing = player.limbAnimator.getPos(tickDelta);
        float limbAmount = player.limbAnimator.getSpeed(tickDelta);
        float handSwing = player.getHandSwingProgress(tickDelta);
        float sneak = player.isSneaking() ? 1.0f : 0.0f;

        Vec3d velocity = player.getVelocity();
        int heldMain = ItemColors.rgb(player.getMainHandStack());
        int heldOff = ItemColors.rgb(player.getOffHandStack());

        SharedMemory.Seqlock lock = sm.poseLock();
        int seq = lock.beginWrite();
        int off = (int) Proto.OFF_POSE;
        sm.buf.putInt(off, seq);
        sm.buf.putInt(off + (int) Proto.POSE_OFF_FLAGS, flags);
        sm.buf.putFloat(off + (int) Proto.POSE_OFF_BODY_YAW, MathHelper.wrapDegrees(player.bodyYaw));
        sm.buf.putFloat(off + (int) Proto.POSE_OFF_HEAD_YAW, MathHelper.wrapDegrees(player.getYaw() - player.bodyYaw));
        sm.buf.putFloat(off + (int) Proto.POSE_OFF_HEAD_PITCH, player.getPitch());
        sm.buf.putFloat(off + (int) Proto.POSE_OFF_LIMB_SWING, limbSwing);
        sm.buf.putFloat(off + (int) Proto.POSE_OFF_LIMB_AMOUNT, limbAmount);
        sm.buf.putFloat(off + (int) Proto.POSE_OFF_HAND_SWING, handSwing);
        sm.buf.putFloat(off + (int) Proto.POSE_OFF_SNEAK, sneak);
        sm.buf.putFloat(off + (int) Proto.POSE_OFF_LEAN, leanAngle(player, velocity));
        sm.buf.putFloat(off + (int) Proto.POSE_OFF_SCALE, 1.0f);
        sm.buf.putFloat(off + (int) Proto.POSE_OFF_VEL_X, (float) velocity.x);
        sm.buf.putFloat(off + (int) Proto.POSE_OFF_VEL_Y, (float) velocity.y);
        sm.buf.putFloat(off + (int) Proto.POSE_OFF_VEL_Z, (float) velocity.z);
        sm.buf.putInt(off + (int) Proto.POSE_OFF_HELD_MAIN, heldMain);
        sm.buf.putInt(off + (int) Proto.POSE_OFF_HELD_OFF, heldOff);
        sm.buf.putFloat(off + (int) Proto.POSE_OFF_FALL, player.fallDistance);
        sm.buf.putFloat(off + (int) Proto.POSE_OFF_HURT, player.hurtTime / 10.0f);
        lock.endWrite(seq);
    }

    /** Swimming and elytra flight tip the whole model forward; everything else stands upright. */
    private static float leanAngle(ClientPlayerEntity player, Vec3d velocity) {
        if (player.isFallFlying()) {
            double speed = velocity.length();
            return (float) MathHelper.clamp(-90.0 * speed, -90.0, 0.0);
        }
        if (player.isSwimming()) {
            return -90.0f;
        }
        return 0.0f;
    }
}
