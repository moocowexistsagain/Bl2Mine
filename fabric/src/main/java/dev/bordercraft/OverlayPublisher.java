// BorderCraft — frame publisher (Minecraft side of the Phase 1a composite).
//
// Each frame, copy Minecraft's framebuffer into the shared-memory overlay double buffer so
// BL2's compositor can draw it inside the Borderlands window. The publish path (pixel copy +
// slot swap) is real and protocol-tested; the GL readback is the one place that needs in-game
// verification against your Yarn/Minecraft version (see the TODO below).
package dev.bordercraft;

import dev.bordercraft.link.SharedMemory;
import net.minecraft.client.MinecraftClient;
import org.lwjgl.opengl.GL11;

import java.nio.ByteBuffer;

public final class OverlayPublisher {
    private final SharedMemory sm;
    private ByteBuffer scratch;

    public OverlayPublisher(SharedMemory sm) {
        this.sm = sm;
    }

    /** Capture the current frame and publish it. Call once per rendered frame (client thread). */
    public long publishFrame(MinecraftClient client) {
        if (client.getFramebuffer() == null || client.getWindow() == null) return -1;
        int w = Math.min(client.getWindow().getFramebufferWidth(), dev.bordercraft.link.Proto.MAX_OVERLAY_W);
        int h = Math.min(client.getWindow().getFramebufferHeight(), dev.bordercraft.link.Proto.MAX_OVERLAY_H);
        if (w <= 0 || h <= 0) return -1;

        int need = w * h * 4;
        if (scratch == null || scratch.capacity() < need) {
            scratch = ByteBuffer.allocateDirect(need);
        }
        scratch.clear();
        scratch.limit(need);

        // TODO(Phase 1a, verify in-game): read the framebuffer's color attachment.
        // Exact calls depend on your Yarn mapping - you want the GL_READ_FRAMEBUFFER bound to
        // client.getFramebuffer()'s fbo, then glReadPixels(0, 0, w, h, GL_BGRA, UNSIGNED_BYTE).
        // GL's origin is bottom-left, so the frame is published bottom-up (flag bit0 = 1).
        client.getFramebuffer().beginWrite(false);
        GL11.glReadPixels(0, 0, w, h, GL11.GL_BGRA, GL11.GL_UNSIGNED_BYTE, scratch);
        scratch.limit(need).position(0);

        return sm.overlay().publish(w, h, scratch, true);
    }
}
