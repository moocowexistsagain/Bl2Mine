// BorderCraft - the blocks a player places in the void world, made visible inside Pandora.
//
// The Minecraft side owns the blocks; Borderlands 2 only draws them. Rather than hooking block
// place/break callbacks (which miss pistons, water flow, fire, gravity falls and world edits),
// this mirrors ground truth: a rolling incremental scan of the column region around the player,
// diffed against the previous snapshot. Anything that changes a block - however it changed -
// shows up, and the diff produces the place/break events the BL2 side animates.
package dev.bordercraft.bridge;

import dev.bordercraft.link.Proto;
import dev.bordercraft.link.SharedMemory;
import net.minecraft.block.BlockState;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.MathHelper;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public final class BlockMirror {
    /** Half-width of the mirrored region, in columns. 16 -> a 33x33 footprint. */
    private static final int RADIUS = 16;
    /** Vertical half-extent around the player's feet. */
    private static final int HEIGHT = 12;
    /** Columns re-scanned per client tick; the whole region refreshes in under a second. */
    private static final int COLUMNS_PER_TICK = 96;

    private final SharedMemory sm;
    private final ByteBuffer eventScratch =
            ByteBuffer.allocate(Proto.EVENT_ENTRY_BYTES).order(ByteOrder.LITTLE_ENDIAN);
    /** Packed block position -> 0xFFRRGGBB with the protocol flags in the top byte. */
    private final Map<Long, Integer> known = new HashMap<>();
    private final BlockPos.Mutable cursor = new BlockPos.Mutable();

    private int scanCursor = 0;
    private int originX = Integer.MIN_VALUE;
    private int originZ = Integer.MIN_VALUE;
    private int revision = 0;
    private int worldId = 0;
    private boolean dirty = true;

    public BlockMirror(SharedMemory sm) {
        this.sm = sm;
    }

    public int trackedBlocks() {
        return known.size();
    }

    public void tick(MinecraftClient client, ClientPlayerEntity player) {
        ClientWorld world = client.world;
        if (world == null) {
            return;
        }
        int id = world.getRegistryKey().getValue().toString().hashCode();
        int centreX = MathHelper.floor(player.getX());
        int centreZ = MathHelper.floor(player.getZ());
        if (id != worldId) {
            // A different dimension is a different world: nothing carries over.
            worldId = id;
            known.clear();
            dirty = true;
        }
        if (Math.abs(centreX - originX) > RADIUS / 2 || Math.abs(centreZ - originZ) > RADIUS / 2) {
            // The player walked out of the region: recentre and drop what fell outside it.
            originX = centreX;
            originZ = centreZ;
            scanCursor = 0;
            known.keySet().removeIf(key -> {
                int x = unpackX(key);
                int z = unpackZ(key);
                return Math.abs(x - originX) > RADIUS || Math.abs(z - originZ) > RADIUS;
            });
            dirty = true;
        }

        scanColumns(world, player);
        if (dirty) {
            publish(player);
            dirty = false;
        }
    }

    private void scanColumns(ClientWorld world, ClientPlayerEntity player) {
        int span = RADIUS * 2 + 1;
        int totalColumns = span * span;
        int feet = MathHelper.floor(player.getY());
        int minY = Math.max(world.getBottomY(), feet - HEIGHT);
        int maxY = Math.min(world.getTopY() - 1, feet + HEIGHT);

        for (int n = 0; n < COLUMNS_PER_TICK; n++) {
            int index = scanCursor++ % totalColumns;
            int x = originX - RADIUS + (index % span);
            int z = originZ - RADIUS + (index / span);
            for (int y = minY; y <= maxY; y++) {
                cursor.set(x, y, z);
                BlockState state = world.getBlockState(cursor);
                long key = pack(x, y, z);
                if (state.isAir()) {
                    Integer previous = known.remove(key);
                    if (previous != null) {
                        dirty = true;
                        pushEvent(Proto.EVT_BLOCK_BREAK, x, y, z, previous);
                    }
                    continue;
                }
                int packed = describe(world, state, x, y, z);
                Integer previous = known.put(key, packed);
                if (previous == null) {
                    dirty = true;
                    pushEvent(Proto.EVT_BLOCK_PLACE, x, y, z, packed);
                } else if (previous != packed) {
                    dirty = true;   // same block position, different look: redraw, no event
                }
            }
        }
        if (scanCursor >= totalColumns) {
            scanCursor %= totalColumns;
        }
    }

    /** Pack colour and protocol flags into one int so the diff is a single comparison. */
    private int describe(ClientWorld world, BlockState state, int x, int y, int z) {
        cursor.set(x, y, z);
        int rgb;
        try {
            rgb = state.getMapColor(world, cursor).color;
        } catch (Throwable ignored) {
            rgb = 0x808080;
        }
        if (rgb == 0) {
            rgb = 0x6E6E6E;     // MapColor.CLEAR: glass, barriers - draw them as faint grey
        }
        int flags = 0;
        if (state.isOpaqueFullCube(world, cursor)) flags |= Proto.BLOCK_FULL_CUBE;
        else flags |= Proto.BLOCK_TRANSLUCENT;
        if (state.getLuminance() > 0) flags |= Proto.BLOCK_EMISSIVE;
        return (flags << 24) | (rgb & 0xFFFFFF);
    }

    /**
     * Publish the block table, nearest first, so the 2048-entry cap always keeps the blocks the
     * player is standing next to rather than an arbitrary slice of the region.
     */
    private void publish(ClientPlayerEntity player) {
        List<Map.Entry<Long, Integer>> entries = new ArrayList<>(known.entrySet());
        final double px = player.getX();
        final double py = player.getY();
        final double pz = player.getZ();
        entries.sort((a, b) -> Double.compare(distanceSq(a.getKey(), px, py, pz),
                distanceSq(b.getKey(), px, py, pz)));
        int count = Math.min(entries.size(), Proto.MAX_BLOCKS);

        SharedMemory.Seqlock lock = sm.blockTableLock();
        int seq = lock.beginWrite();
        int off = (int) Proto.OFF_BLOCKS;
        sm.buf.putInt(off, seq);
        sm.buf.putInt(off + (int) Proto.BLOCKS_OFF_COUNT, count);
        sm.buf.putInt(off + (int) Proto.BLOCKS_OFF_WORLD_ID, worldId);
        sm.buf.putInt(off + (int) Proto.BLOCKS_OFF_REVISION, ++revision);
        for (int i = 0; i < count; i++) {
            Map.Entry<Long, Integer> e = entries.get(i);
            int base = off + (int) Proto.BLOCKS_OFF_ENTRIES + i * Proto.BLOCK_ENTRY_BYTES;
            long key = e.getKey();
            int packed = e.getValue();
            sm.buf.putInt(base, unpackX(key));
            sm.buf.putInt(base + 4, unpackY(key));
            sm.buf.putInt(base + 8, unpackZ(key));
            sm.buf.put(base + 12, (byte) ((packed >> 16) & 0xFF));
            sm.buf.put(base + 13, (byte) ((packed >> 8) & 0xFF));
            sm.buf.put(base + 14, (byte) (packed & 0xFF));
            sm.buf.put(base + 15, (byte) ((packed >>> 24) & 0xFF));
        }
        lock.endWrite(seq);
    }

    /**
     * The actor-id field carries the packed RGB for block events: the BL2 voxel field reads it
     * there (bl2sdk/BorderCraft/voxel.py, BlockField.apply_event) so a freshly placed block is
     * drawn on the very next frame, without waiting for the next full table.
     */
    private void pushEvent(int type, int x, int y, int z, int packed) {
        SharedMemory.pushEvent(sm.eventRing(), eventScratch, type, (packed >>> 24) & 0xFF,
                packed & 0xFFFFFF, 0.0f, 0.0f, 0.0f, x, y, z);
    }

    private static double distanceSq(long key, double px, double py, double pz) {
        double dx = unpackX(key) + 0.5 - px;
        double dy = unpackY(key) + 0.5 - py;
        double dz = unpackZ(key) + 0.5 - pz;
        return dx * dx + dy * dy + dz * dz;
    }

    // 21 bits X, 21 bits Z (signed), 12 bits Y biased by 2048: enough for the whole build range.
    private static long pack(int x, int y, int z) {
        return ((long) (x & 0x1FFFFF) << 43) | ((long) (z & 0x1FFFFF) << 22)
                | ((y + 2048) & 0xFFFL);
    }

    private static int unpackX(long key) {
        return (int) (key << 0 >> 43);
    }

    private static int unpackZ(long key) {
        return (int) (key << 21 >> 43);
    }

    private static int unpackY(long key) {
        return (int) (key & 0xFFFL) - 2048;
    }
}
