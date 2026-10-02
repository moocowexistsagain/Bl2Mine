// BorderCraft — authenticated Minecraft skin publisher.
//
// BL2's Python SDK cannot safely upload arbitrary BGRA pixels into a UE3/D3D9 texture. A 64x64
// skin is small enough to send through the existing overlay slots and draw as pixel-art rectangles
// with Engine.Canvas, which gives us a native-free, controllable avatar vertical slice.
package dev.bordercraft;

import com.mojang.blaze3d.systems.RenderSystem;
import dev.bordercraft.link.Proto;
import dev.bordercraft.link.SharedMemory;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.texture.NativeImage;
import net.minecraft.client.util.SkinTextures;
import net.minecraft.util.Identifier;

import java.nio.ByteBuffer;

public final class SkinPublisher {
    private static final int SKIN_SIZE = 64;
    private static final long REFRESH_NS = 2_000_000_000L;

    private final SharedMemory.Overlay overlay;
    private final ByteBuffer bgra = ByteBuffer.allocateDirect(SKIN_SIZE * SKIN_SIZE * 4);
    private long nextCaptureNs;
    private long nextErrorLogNs;
    private Identifier lastSkin;

    public SkinPublisher(SharedMemory sm) {
        this.overlay = sm.overlay();
    }

    /**
     * Read the active authenticated-player skin from its GL texture and publish it as BGRA.
     *
     * <p>This is called by Minecraft's client tick on the render thread. It intentionally refreshes
     * every two seconds even when the identifier is unchanged: the skin manager can asynchronously
     * replace the fallback texture under that same identifier after login.</p>
     */
    public long publishIfDue(MinecraftClient client) {
        long now = System.nanoTime();
        if (now < nextCaptureNs || client.player == null || !RenderSystem.isOnRenderThread()) {
            return -1;
        }
        nextCaptureNs = now + REFRESH_NS;

        SkinTextures skin = client.player.getSkinTextures();
        Identifier texture = skin.texture();
        try (NativeImage image = new NativeImage(SKIN_SIZE, SKIN_SIZE, false)) {
            // PlayerSkinTexture normally keeps pixels only on the GPU. Binding the active texture
            // and reading mip zero works for both downloaded skins and Minecraft's fallback skins.
            client.getTextureManager().bindTexture(texture);
            image.loadFromTextureImage(0, false);

            bgra.clear();
            for (int y = 0; y < SKIN_SIZE; y++) {
                for (int x = 0; x < SKIN_SIZE; x++) {
                    // NativeImage#getColor is ABGR as an int (little-endian RGBA in memory).
                    int abgr = image.getColor(x, y);
                    bgra.put((byte) ((abgr >>> 16) & 0xff)); // B
                    bgra.put((byte) ((abgr >>> 8) & 0xff));  // G
                    bgra.put((byte) (abgr & 0xff));          // R
                    bgra.put((byte) ((abgr >>> 24) & 0xff)); // A
                }
            }
            bgra.flip();

            int flags = Proto.OVERLAY_SKIN;
            if (skin.model() == SkinTextures.Model.SLIM) flags |= Proto.OVERLAY_SLIM;
            long frame = overlay.publish(SKIN_SIZE, SKIN_SIZE, bgra, flags);
            if (!texture.equals(lastSkin)) {
                BorderCraftMod.LOG.info("BorderCraft: published player skin {} ({})",
                        texture, skin.model().getName());
                lastSkin = texture;
            }
            return frame;
        } catch (RuntimeException error) {
            // Texture download/loading races are expected during world join. Retry quietly, but
            // leave a rate-limited breadcrumb for real driver/API failures.
            if (now >= nextErrorLogNs) {
                BorderCraftMod.LOG.warn("BorderCraft: skin readback will retry", error);
                nextErrorLogNs = now + 10_000_000_000L;
            }
            return -1;
        }
    }
}
