// BorderCraft - Pandora's inhabitants, as far as Minecraft needs to know about them.
//
// Borderlands 2 publishes every nearby pawn - skags, bandits, loaders, bosses - with its
// position, size and health. Minecraft does not spawn entities for them (registering an entity
// type blind against a moving mapping surface is a liability); instead the bridge keeps their
// boxes here and raycasts the crosshair against them, which is all that melee combat and the
// "use" interaction actually need.
package dev.bordercraft.bridge;

import dev.bordercraft.link.Proto;
import dev.bordercraft.link.SharedMemory;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Vec3d;

import java.util.Optional;

public final class ActorMirror {
    public static final class Actor {
        public int id;
        public int flags;
        public double x, y, z;
        public float yaw;
        public float health, maxHealth;
        public float halfWidth, height;

        public Box box() {
            return new Box(x - halfWidth, y, z - halfWidth, x + halfWidth, y + height, z + halfWidth);
        }

        public boolean isAlive() {
            return (flags & Proto.ACTOR_DEAD) == 0 && health > 0.0f;
        }

        public boolean isHostile() {
            return (flags & Proto.ACTOR_HOSTILE) != 0;
        }
    }

    private final SharedMemory sm;
    private Actor[] actors = new Actor[0];
    public int count = 0;

    public ActorMirror(SharedMemory sm) {
        this.sm = sm;
        this.actors = new Actor[Proto.MAX_ACTORS];
        for (int i = 0; i < actors.length; i++) {
            actors[i] = new Actor();
        }
    }

    /** Snapshot the actor table under its seqlock. Leaves the previous snapshot on a torn read. */
    public void refresh() {
        SharedMemory.Seqlock lock = sm.actorTableLock();
        int seq = lock.tryRead();
        if (seq < 0) {
            return;
        }
        int base = (int) Proto.OFF_ACTOR_TABLE;
        int n = Math.min(sm.buf.getInt(base + 4), Proto.MAX_ACTORS);
        for (int i = 0; i < n; i++) {
            int o = base + (int) Proto.ACTOR_OFF_ENTRIES + i * Proto.ACTOR_ENTRY_BYTES;
            Actor a = actors[i];
            a.id = sm.buf.getInt(o + (int) Proto.ACTOR_OFF_ID);
            a.flags = sm.buf.getInt(o + (int) Proto.ACTOR_OFF_FLAGS);
            a.x = sm.buf.getFloat(o + (int) Proto.ACTOR_OFF_X);
            a.y = sm.buf.getFloat(o + (int) Proto.ACTOR_OFF_Y);
            a.z = sm.buf.getFloat(o + (int) Proto.ACTOR_OFF_Z);
            a.yaw = sm.buf.getFloat(o + (int) Proto.ACTOR_OFF_YAW);
            a.health = sm.buf.getFloat(o + (int) Proto.ACTOR_OFF_HEALTH);
            a.maxHealth = sm.buf.getFloat(o + (int) Proto.ACTOR_OFF_MAX_HEALTH);
            a.halfWidth = sm.buf.getFloat(o + (int) Proto.ACTOR_OFF_HALF_W);
            a.height = sm.buf.getFloat(o + (int) Proto.ACTOR_OFF_HEIGHT);
        }
        if (!lock.readOk(seq)) {
            return;   // BL2 rewrote the table mid-copy; keep last tick's snapshot
        }
        count = n;
    }

    public Actor get(int index) {
        return actors[index];
    }

    /**
     * The first living actor the player is looking at within {@code reach}.
     *
     * <p>Boxes are expanded slightly, matching how vanilla gives entity attacks a little
     * tolerance (ProjectileUtil uses a 0.3 expansion for the same reason).</p>
     */
    public Optional<Actor> raycast(Vec3d eye, Vec3d direction, double reach) {
        Vec3d end = eye.add(direction.multiply(reach));
        Actor best = null;
        double bestDistance = Double.MAX_VALUE;
        for (int i = 0; i < count; i++) {
            Actor a = actors[i];
            if (!a.isAlive()) {
                continue;
            }
            Box box = a.box().expand(0.3);
            Optional<Vec3d> hit = box.raycast(eye, end);
            if (hit.isEmpty()) {
                if (!box.contains(eye)) {
                    continue;
                }
                hit = Optional.of(eye);
            }
            double distance = eye.squaredDistanceTo(hit.get());
            if (distance < bestDistance) {
                bestDistance = distance;
                best = a;
            }
        }
        return Optional.ofNullable(best);
    }

    /** The nearest living actor within {@code radius}, used for the "use/approach" action. */
    public Optional<Actor> nearest(Vec3d point, double radius) {
        Actor best = null;
        double bestDistance = radius * radius;
        for (int i = 0; i < count; i++) {
            Actor a = actors[i];
            if (!a.isAlive()) {
                continue;
            }
            double dx = a.x - point.x;
            double dy = a.y + a.height * 0.5 - point.y;
            double dz = a.z - point.z;
            double distance = dx * dx + dy * dy + dz * dz;
            if (distance < bestDistance) {
                bestDistance = distance;
                best = a;
            }
        }
        return Optional.ofNullable(best);
    }
}
