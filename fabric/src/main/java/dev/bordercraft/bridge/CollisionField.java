// BorderCraft - Pandora's collision, injected into Minecraft's own physics.
//
// Borderlands 2 streams the shape of the world around the player as heightfield records at 1/8
// block resolution (see protocol/bordercraft_protocol.h). This class decodes them into
// VoxelShapes and hands them to Minecraft's collision solver through EntityCollisionMixin.
//
// These are deliberately NOT blocks: the mirror world stays a void world the player can build in
// freely, and Pandora's geometry sits alongside the blocks they place. Everything that makes
// Minecraft movement feel like Minecraft - gravity, 0.6 step-up, sprint jumping, sneaking at an
// edge, slab-height precision - is Minecraft's unmodified code running against these shapes.
package dev.bordercraft.bridge;

import dev.bordercraft.link.Proto;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.MathHelper;
import net.minecraft.util.shape.VoxelShape;
import net.minecraft.util.shape.VoxelShapes;

import java.nio.ByteBuffer;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

public final class CollisionField {
    /** One shared field: the mixin runs on both the client and the integrated server thread. */
    public static final CollisionField INSTANCE = new CollisionField();

    private static final int SECTION = 16;
    private static final int SUB = 8;              // heightfield units per block
    private static final short NO_HEIGHT = -32768;
    /** How far below the surface a column is solid. Deep enough that nothing falls through. */
    private static final double COLUMN_DEPTH = 6.0;
    /** Extra wall height given to a surface too steep to walk on. */
    private static final double STEEP_WALL = 3.0;

    private final Map<Long, Section> sections = new ConcurrentHashMap<>();
    private volatile int epoch = -1;
    private volatile boolean enabled = true;
    public volatile int sectionsLoaded = 0;

    private CollisionField() {}

    private static final class Section {
        final short[] heights = new short[SECTION * SECTION];
        final byte[] flags = new byte[SECTION * SECTION];
    }

    private static long key(int sectionX, int sectionZ) {
        return (((long) sectionX) << 32) ^ (sectionZ & 0xFFFFFFFFL);
    }

    public void setEnabled(boolean value) {
        enabled = value;
    }

    public boolean isEnabled() {
        return enabled;
    }

    public int epoch() {
        return epoch;
    }

    /** A new Borderlands 2 map invalidates everything Minecraft is standing on. */
    public void clear(int newEpoch) {
        sections.clear();
        sectionsLoaded = 0;
        epoch = newEpoch;
    }

    /**
     * Decode one collision record straight out of the ring buffer.
     *
     * @return true when the record was accepted (matching epoch and a supported kind)
     */
    public boolean accept(ByteBuffer record, int currentEpoch) {
        record.position(0);
        record.getInt();                       // seq, unused by the consumer
        int recordEpoch = record.getInt();
        short sectionX = record.getShort();
        record.getShort();                     // section Y: heightfields are 2D, keyed by X/Z
        short sectionZ = record.getShort();
        int kind = record.get() & 0xFF;
        if (recordEpoch != currentEpoch) {
            return false;                      // stale data from before a map change
        }
        if (epoch != currentEpoch) {
            clear(currentEpoch);
        }
        if (kind != Proto.COL_HEIGHTFIELD) {
            return false;                      // AABB payloads arrive with the Stage C exporter
        }
        Section section = new Section();
        for (int i = 0; i < SECTION * SECTION; i++) {
            int offset = 0x40 + i * 4;
            section.heights[i] = record.getShort(offset);
            section.flags[i] = record.get(offset + 2);
        }
        if (sections.put(key(sectionX, sectionZ), section) == null) {
            sectionsLoaded = sections.size();
        }
        return true;
    }

    /** Forget sections further than {@code radius} sections from the player. */
    public void evictFarSections(double playerX, double playerZ, int radius) {
        int centreX = MathHelper.floor(playerX / SECTION);
        int centreZ = MathHelper.floor(playerZ / SECTION);
        sections.keySet().removeIf(k -> {
            int sx = (int) (k >> 32);
            int sz = (int) (long) k;
            return Math.abs(sx - centreX) > radius || Math.abs(sz - centreZ) > radius;
        });
        sectionsLoaded = sections.size();
    }

    /**
     * Every Borderlands 2 collision shape overlapping {@code box}.
     *
     * <p>Called from Minecraft's movement path, so it allocates nothing when the field is empty
     * (the overwhelmingly common case while no Borderlands 2 host is attached).</p>
     */
    public List<VoxelShape> collect(Box box) {
        if (!enabled || sections.isEmpty()) {
            return List.of();
        }
        int minX = MathHelper.floor(box.minX) - 1;
        int maxX = MathHelper.floor(box.maxX) + 1;
        int minZ = MathHelper.floor(box.minZ) - 1;
        int maxZ = MathHelper.floor(box.maxZ) + 1;
        List<VoxelShape> shapes = null;
        for (int x = minX; x <= maxX; x++) {
            for (int z = minZ; z <= maxZ; z++) {
                Section section = sections.get(key(Math.floorDiv(x, SECTION), Math.floorDiv(z, SECTION)));
                if (section == null) {
                    continue;
                }
                int index = Math.floorMod(z, SECTION) * SECTION + Math.floorMod(x, SECTION);
                short raw = section.heights[index];
                if (raw == NO_HEIGHT) {
                    continue;
                }
                double top = raw / (double) SUB;
                int flags = section.flags[index] & 0xFF;
                if ((flags & Proto.COL_WATER) != 0) {
                    continue;                  // water is buoyancy, not collision
                }
                if ((flags & Proto.COL_STEEP) != 0) {
                    top += STEEP_WALL;         // too steep to walk: treat the column as a wall
                }
                double bottom = top - COLUMN_DEPTH;
                if (top <= box.minY || bottom >= box.maxY) {
                    continue;
                }
                if (shapes == null) {
                    shapes = new ArrayList<>();
                }
                shapes.add(VoxelShapes.cuboid(x, bottom, z, x + 1.0, top, z + 1.0));
            }
        }
        return shapes == null ? List.of() : shapes;
    }

    /** Surface height at a column, or {@link Double#NaN} when Borderlands 2 has not sent it. */
    public double surfaceAt(int x, int z) {
        Section section = sections.get(key(Math.floorDiv(x, SECTION), Math.floorDiv(z, SECTION)));
        if (section == null) {
            return Double.NaN;
        }
        short raw = section.heights[Math.floorMod(z, SECTION) * SECTION + Math.floorMod(x, SECTION)];
        return raw == NO_HEIGHT ? Double.NaN : raw / (double) SUB;
    }
}
