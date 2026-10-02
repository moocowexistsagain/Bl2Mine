// BorderCraft - Fabric mod entrypoint (Minecraft side of the bridge).
//
// Minecraft runs headless-ish in the background while Borderlands 2 is the window you look at.
// Every client tick this orchestrator:
//
//   BL2 -> MC   state (teleports, map changes), the collision of Pandora, replayed input,
//               and damage dealt to the player by bandits.
//   MC -> BL2   McState (where the player is and how the camera should move), the player's
//               pose for the 3D model, the HUD and the whole inventory, the blocks the player
//               has placed, hits on mirrored pawns, and the 64x64 skin.
//
// Nothing here reimplements Minecraft: movement, mining, inventory and combat are vanilla code
// acting on vanilla state. The bridge only moves that state across the process boundary.
package dev.bordercraft;

import dev.bordercraft.bridge.ActorMirror;
import dev.bordercraft.bridge.BlockMirror;
import dev.bordercraft.bridge.CollisionField;
import dev.bordercraft.bridge.DamageBridge;
import dev.bordercraft.bridge.HudPublisher;
import dev.bordercraft.bridge.InputReplayer;
import dev.bordercraft.bridge.PhysicsBridge;
import dev.bordercraft.bridge.PosePublisher;
import dev.bordercraft.link.Proto;
import dev.bordercraft.link.SharedMemory;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.util.hit.HitResult;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.file.Path;

public class BorderCraftMod implements ClientModInitializer {
    public static final String MOD_ID = "bordercraft";
    public static final Logger LOG = LoggerFactory.getLogger("bordercraft");

    private SharedMemory sm;
    private SkinPublisher skinPublisher;
    private PhysicsBridge physics;
    private PosePublisher pose;
    private HudPublisher hud;
    private BlockMirror blocks;
    private InputReplayer input;
    private ActorMirror actors;
    private DamageBridge damage;

    private final ByteBuffer eventScratch =
            ByteBuffer.allocate(Proto.EVENT_ENTRY_BYTES).order(ByteOrder.LITTLE_ENDIAN);

    private long frames = 0;
    private long lastOpenAttempt = 0;
    private boolean wasDead = false;
    private boolean menuWasOpen = false;

    @Override
    public void onInitializeClient() {
        LOG.info("BorderCraft: starting bridge client");
        ClientTickEvents.END_CLIENT_TICK.register(this::tick);
    }

    private void tick(MinecraftClient client) {
        if (sm == null && !tryOpen()) {
            return;
        }
        sm.heartbeat(false);
        frames++;

        ClientPlayerEntity player = client.player;
        if (player == null || client.world == null) {
            // Title screen or a world load in progress: keep the heartbeat alive and wait.
            CollisionField.INSTANCE.setEnabled(false);
            return;
        }
        CollisionField.INSTANCE.setEnabled(true);

        try {
            runTick(client, player);
        } catch (Throwable t) {
            // A bridge fault must never take the game down with it.
            LOG.error("BorderCraft: tick failed", t);
        }
    }

    private void runTick(MinecraftClient client, ClientPlayerEntity player) {
        // ---- BL2 -> MC -----------------------------------------------------------------------
        int bl2Flags = physics.consumeBl2State(client, player);
        physics.drainCollision(player);

        boolean menuOpen = (bl2Flags & (Proto.BL2_MENU_OPEN | Proto.BL2_LOADING | Proto.BL2_PAUSED)) != 0;
        if (menuOpen) {
            if (!menuWasOpen) {
                // Borderlands 2 took the keyboard: drop everything the bridge is holding down,
                // otherwise the player keeps walking while reading their inventory.
                input.releaseAll();
            }
        } else {
            input.drain(client, player);
        }
        menuWasOpen = menuOpen;

        actors.refresh();
        damage.drain(client, player);

        // Attack and use: vanilla already resolved them against blocks and its own entities;
        // anything it missed belongs to Pandora.
        int attacks = input.takeAttacks();
        int interacts = input.takeInteracts();
        if (attacks > 0 && blockTargetDistance(client) > 1.0) {
            for (int i = 0; i < attacks; i++) {
                damage.onAttack(player);
            }
        }
        if (interacts > 0 && client.crosshairTarget != null
                && client.crosshairTarget.getType() == HitResult.Type.MISS) {
            damage.onInteract(player);
        }

        // ---- MC -> BL2 -----------------------------------------------------------------------
        physics.publishMcState(client, player, frames);
        pose.publish(client, player);
        hud.publish(client, player);
        blocks.tick(client, player);
        skinPublisher.publishIfDue(client);

        boolean dead = player.isDead() || player.getHealth() <= 0.0f;
        if (dead != wasDead) {
            // Minecraft's death flow owns the moment: Borderlands 2 hands control back to its
            // own pawn until the player respawns.
            SharedMemory.pushEvent(sm.eventRing(), eventScratch,
                    dead ? Proto.EVT_PLAYER_DIED : Proto.EVT_PLAYER_RESPAWNED, 0, 0,
                    player.getHealth(), 0, 0,
                    (float) player.getX(), (float) player.getY(), (float) player.getZ());
            wasDead = dead;
        }
    }

    /** Distance to whatever vanilla's crosshair is on, or a large number when it is on nothing. */
    private static double blockTargetDistance(MinecraftClient client) {
        HitResult target = client.crosshairTarget;
        if (target == null || target.getType() == HitResult.Type.MISS || client.player == null) {
            return Double.MAX_VALUE;
        }
        return target.getPos().distanceTo(client.player.getEyePos());
    }

    private boolean tryOpen() {
        long now = System.currentTimeMillis();
        if (now - lastOpenAttempt < 1000) {
            return false;   // BL2 is not up: retry once a second, not twenty times
        }
        lastOpenAttempt = now;
        try {
            Path path = SharedMemory.defaultPath();
            sm = SharedMemory.open(path);
            sm.writeHeader(0, (int) ProcessHandle.current().pid());
            skinPublisher = new SkinPublisher(sm);
            physics = new PhysicsBridge(sm);
            pose = new PosePublisher(sm);
            hud = new HudPublisher(sm);
            blocks = new BlockMirror(sm);
            input = new InputReplayer(sm);
            actors = new ActorMirror(sm);
            damage = new DamageBridge(sm, actors);
            LOG.info("BorderCraft: bridge open at {}", path);
            return true;
        } catch (Exception e) {
            sm = null;
            return false;
        }
    }
}
