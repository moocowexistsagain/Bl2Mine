// BorderCraft — frame publisher (Minecraft side of the Phase 1a composite).
//
// Each frame, copy Minecraft's framebuffer into the shared-memory overlay double buffer so
// BL2's compositor can draw it inside the Borderlands window. The publish path (pixel copy +
// slot swap) is real and protocol-tested; the GL readback still needs in-game verification
// against the target Minecraft/Yarn version.
package dev.bordercraft;

import dev.bordercraft.link.SharedMemory;
import net.minecraft.client.MinecraftClient;
import org.lwjgl.opengl.GL11;

import java.nio.ByteBuffer;

public final class OverlayPublisher {
    private final SharedMemory.Overlay overlay;
    private ByteBuffer scratch;

    public OverlayPublisher(SharedMemory sm) {
        this.overlay = sm.overlay();
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

        // The HUD render callback runs after Minecraft's in-world HUD has drawn. Bind the main
        // framebuffer explicitly so the readback does not depend on whatever target a HUD mod
        // left bound. GL's origin is bottom-left, so publish bottom-up (flag bit0 = 1).
        client.getFramebuffer().beginWrite(false);
        GL11.glReadPixels(0, 0, w, h, GL11.GL_BGRA, GL11.GL_UNSIGNED_BYTE, scratch);
        scratch.limit(need).position(0);

        return overlay.publish(w, h, scratch, true);
    }
}
