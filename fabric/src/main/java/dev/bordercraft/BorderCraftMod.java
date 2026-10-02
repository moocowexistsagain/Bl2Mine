// BorderCraft — Fabric mod entrypoint (Minecraft side of the bridge).
//
// Runs hidden next to Borderlands 2. Each client tick it publishes McState, consumes Bl2State and
// drains the input/event/collision channels. The gameplay pieces (PhysicsInjector, InputReplayer,
// ActorProxy, HudMirror) are stubs at this stage — see docs/DESIGN.md for what each becomes.
package dev.bordercraft;

import dev.bordercraft.link.Proto;
import dev.bordercraft.link.SharedMemory;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.nio.ByteBuffer;
import java.nio.file.Path;

public class BorderCraftMod implements ClientModInitializer {
    public static final String MOD_ID = "bordercraft";
    public static final Logger LOG = LoggerFactory.getLogger("bordercraft");

    private SharedMemory sm;
    private final ByteBuffer scratch = ByteBuffer.allocate(Proto.COLLISION_REC_BYTES);
    private OverlayPublisher publisher;
    private long frames = 0;

    @Override
    public void onInitializeClient() {
        LOG.info("BorderCraft: starting bridge client");
        ClientTickEvents.END_CLIENT_TICK.register(client -> tick(client));
    }

    private void tick(net.minecraft.client.MinecraftClient client) {
        if (sm == null) {
            if (!tryOpen()) return;
        }

        sm.heartbeat(false);
        frames++;

        // --- consume BL2 -> MC -------------------------------------------------------------
        SharedMemory.Seqlock lock = sm.bl2StateLock();
        int seq = lock.tryRead();
        if (seq >= 0 && lock.readOk(seq)) {
            int flags = sm.buf.getInt((int) (Proto.OFF_BL2_STATE + Proto.BL2_OFF_FLAGS));
            if ((flags & Proto.BL2_MENU_OPEN) != 0) {
                // TODO(InputReplayer): release all held MC keys
            }
            int teleportSeq = sm.buf.getInt((int) (Proto.OFF_BL2_STATE + Proto.BL2_OFF_TELEPORT_SEQ));
            // TODO(PhysicsInjector): apply teleport + feed CollisionField; ack in McState
        }

        // collision ring -> CollisionField (consumer side)
        SharedMemory.Ring cols = sm.collisionRing();
        while (cols.pop(scratch)) {
            // TODO(PhysicsInjector): decode record into the MC CollisionField
        }

        // input ring -> replay as if the MC window had focus
        SharedMemory.Ring inputs = sm.inputRing();
        while (inputs.pop(scratch)) {
            // TODO(InputReplayer): dispatch key/mouse events into MC's input handlers
        }

        // --- publish MC -> BL2 ---------------------------------------------------------------
        publishMcState(client);

        // frame pipeline: capture this frame into the overlay double buffer.
        // TODO(Phase 1a): ideally called from a render-event hook right after the frame is
        // drawn; the tick hook is close enough for the first composite tests.
        if (publisher != null) {
            publisher.publishFrame(client);
        }

        // event ring is produced by hooks (ActorProxy.hurt, block place/break, player death):
        // TODO(HitBridge): push McEvent records on combat/block events
    }

    private void publishMcState(net.minecraft.client.MinecraftClient client) {
        if (client.player == null) return;
        SharedMemory.Seqlock lock = sm.mcStateLock();
        int seq = lock.beginWrite();
        long off = Proto.OFF_MC_STATE;
        sm.buf.putInt((int) (off + 0x00), seq);
        sm.buf.putInt((int) (off + Proto.MC_OFF_FLAGS),
                Proto.MC_IN_WORLD | (client.player.isOnGround() ? Proto.MC_ON_GROUND : 0));
        sm.buf.putDouble((int) (off + Proto.MC_OFF_X), client.player.getX());
        sm.buf.putDouble((int) (off + Proto.MC_OFF_Y), client.player.getY());
        sm.buf.putDouble((int) (off + Proto.MC_OFF_Z), client.player.getZ());
        sm.buf.putFloat((int) (off + Proto.MC_OFF_YAW), client.player.getYaw());
        sm.buf.putFloat((int) (off + Proto.MC_OFF_PITCH), client.player.getPitch());
        sm.buf.putLong((int) (off + Proto.MC_OFF_FRAME_COUNTER), frames);
        lock.endWrite(seq);
    }

    private boolean tryOpen() {
        try {
            Path path = SharedMemory.defaultPath();
            sm = SharedMemory.open(path);
            sm.writeHeader(0, (int) ProcessHandle.current().pid());
            publisher = new OverlayPublisher(sm);
            LOG.info("BorderCraft: bridge open at {}", path);
            return true;
        } catch (Exception e) {
            return false; // BL2 not up yet (or bridge missing); retry next tick
        }
    }
}
