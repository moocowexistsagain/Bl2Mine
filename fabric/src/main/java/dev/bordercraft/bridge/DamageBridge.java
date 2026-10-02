// BorderCraft - combat, in both directions, with each game keeping its own rules.
//
// MC -> BL2: a swing of the Minecraft weapon raycasts against the mirrored pawns and sends the
// damage Minecraft computed (attribute value, cooldown progress, crit) as a number of
// half-hearts. The host scales it into the pawn's health pool, so a diamond sword stays
// meaningful against a level 5 skag and a level 50 badass alike.
//
// BL2 -> MC: a bandit's bullet arrives as a fraction of the player's health and is applied with
// a real DamageSource on the integrated server, so armour, enchantments, absorption, the damage
// tilt, the hurt sound and the death screen are all vanilla behaviour.
package dev.bordercraft.bridge;

import dev.bordercraft.link.Proto;
import dev.bordercraft.link.SharedMemory;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.entity.attribute.EntityAttributes;
import net.minecraft.entity.damage.DamageSource;
import net.minecraft.entity.damage.DamageSources;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.util.math.Vec3d;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.Optional;

public final class DamageBridge {
    /** Minecraft's damage pool. Everything crossing the bridge is a fraction of this. */
    public static final float MC_PLAYER_MAX_HEALTH = 20.0f;
    private static final double MELEE_REACH = 3.5;
    private static final double INTERACT_REACH = 4.0;

    private final SharedMemory sm;
    private final ActorMirror actors;
    private final ByteBuffer eventScratch =
            ByteBuffer.allocate(Proto.EVENT_ENTRY_BYTES).order(ByteOrder.LITTLE_ENDIAN);
    private final ByteBuffer inScratch =
            ByteBuffer.allocate(Proto.EVENT_ENTRY_BYTES).order(ByteOrder.LITTLE_ENDIAN);

    public long hitsSent = 0;
    public long damageTaken = 0;

    public DamageBridge(SharedMemory sm, ActorMirror actors) {
        this.sm = sm;
        this.actors = actors;
    }

    // ---- MC -> BL2 ---------------------------------------------------------------------------

    /** The player swung at something. Resolve it against Pandora and report the hit. */
    public void onAttack(ClientPlayerEntity player) {
        Vec3d eye = player.getEyePos();
        Optional<ActorMirror.Actor> target = actors.raycast(eye, player.getRotationVec(1.0f), MELEE_REACH);
        if (target.isEmpty()) {
            return;
        }
        ActorMirror.Actor actor = target.get();
        float damage = (float) player.getAttributeValue(EntityAttributes.GENERIC_ATTACK_DAMAGE);
        float cooldown = player.getAttackCooldownProgress(0.5f);
        // Vanilla ramps a swing from 20% to 100% over the cooldown: an unrecovered weapon hits
        // for a fraction of its damage, which is a core part of how 1.9+ combat reads.
        damage *= 0.2f + cooldown * cooldown * 0.8f;
        boolean crit = cooldown > 0.9f && player.fallDistance > 0.0f && !player.isOnGround()
                && !player.isClimbing() && !player.isTouchingWater() && !player.isSprinting();
        if (damage <= 0.0f) {
            return;
        }
        SharedMemory.pushEvent(sm.eventRing(), eventScratch, Proto.EVT_PLAYER_HIT_ACTOR, 0,
                actor.id, damage, crit ? 1.0f : 0.0f, 0.0f,
                (float) actor.x, (float) (actor.y + actor.height * 0.5), (float) actor.z);
        player.swingHand(net.minecraft.util.Hand.MAIN_HAND);
        hitsSent++;
    }

    /** The player pressed "use" with nothing in reach: offer it to Pandora (talk, loot, press). */
    public void onInteract(ClientPlayerEntity player) {
        Vec3d eye = player.getEyePos();
        Optional<ActorMirror.Actor> target = actors.raycast(eye, player.getRotationVec(1.0f), INTERACT_REACH);
        if (target.isEmpty()) {
            target = actors.nearest(eye, INTERACT_REACH);
        }
        target.ifPresent(actor -> SharedMemory.pushEvent(sm.eventRing(), eventScratch,
                Proto.EVT_APPROACH_ACTOR, 0, actor.id, 0, 0, 0,
                (float) actor.x, (float) actor.y, (float) actor.z));
    }

    // ---- BL2 -> MC ---------------------------------------------------------------------------

    /** Drain the BL2 -> MC event ring: damage, kills and heals coming out of Borderlands 2. */
    public void drain(MinecraftClient client, ClientPlayerEntity player) {
        SharedMemory.Ring ring = sm.bl2EventRing();
        while (ring.pop(inScratch)) {
            inScratch.position(0);
            int type = inScratch.getShort() & 0xFFFF;
            int kind = inScratch.getShort() & 0xFFFF;
            inScratch.getInt();                 // actor id: the attacker, unused for now
            float a = inScratch.getFloat();
            switch (type) {
                case Proto.EVT_BL2_DAMAGE_PLAYER -> applyDamage(client, player, a, kind);
                case Proto.EVT_BL2_KILL_PLAYER -> applyDamage(client, player, 1.0e9f, kind);
                case Proto.EVT_BL2_HEALED -> heal(client, player, a);
                default -> { }
            }
        }
    }

    private void applyDamage(MinecraftClient client, ClientPlayerEntity player, float hearts, int kind) {
        if (hearts <= 0.0f) {
            return;
        }
        damageTaken++;
        onServer(client, player, serverPlayer -> {
            DamageSources sources = serverPlayer.getDamageSources();
            DamageSource source = switch (kind) {
                case Proto.DAMAGE_EXPLOSION -> sources.explosion(null, null);
                case Proto.DAMAGE_FIRE -> sources.onFire();
                case Proto.DAMAGE_CORROSIVE -> sources.magic();
                case Proto.DAMAGE_SHOCK -> sources.magic();
                case Proto.DAMAGE_FALL -> sources.fall();
                default -> sources.generic();
            };
            // Two-argument damage() is the 1.21.1 signature; the server is authoritative, so
            // armour, absorption, enchantments and the death screen all behave normally.
            serverPlayer.damage(source, hearts);
        });
    }

    private void heal(MinecraftClient client, ClientPlayerEntity player, float hearts) {
        if (hearts <= 0.0f) {
            return;
        }
        onServer(client, player, serverPlayer -> serverPlayer.heal(hearts));
    }

    /**
     * Run an action against the authoritative copy of the player.
     *
     * <p>BorderCraft is a single-player bridge, so the integrated server is in this process; its
     * thread, however, is not this one. Anything that mutates health has to be handed to it.</p>
     */
    private void onServer(MinecraftClient client, ClientPlayerEntity player,
                          java.util.function.Consumer<ServerPlayerEntity> action) {
        MinecraftServer server = client.getServer();
        if (server == null) {
            return;   // connected to a dedicated server: BorderCraft cannot forge damage there
        }
        server.execute(() -> {
            ServerPlayerEntity serverPlayer = server.getPlayerManager().getPlayer(player.getUuid());
            if (serverPlayer != null) {
                action.accept(serverPlayer);
            }
        });
    }
}
