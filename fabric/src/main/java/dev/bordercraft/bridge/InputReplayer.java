// BorderCraft - Borderlands 2's keyboard and mouse, driving the Minecraft client.
//
// Minecraft runs in a background window with no focus, so no real input ever reaches it. The BL2
// host captures WASD, jump, sneak, the hotbar, attack, use and mouse look and pushes them here;
// this class replays them through the same KeyBinding state the real window would have set, so
// Minecraft's own code does the rest - and keeps keys like Escape and Tab for Borderlands 2.
package dev.bordercraft.bridge;

import dev.bordercraft.link.Proto;
import dev.bordercraft.link.SharedMemory;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.gui.screen.Screen;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.client.option.KeyBinding;
import net.minecraft.client.util.InputUtil;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.HashSet;
import java.util.Set;

public final class InputReplayer {
    private final SharedMemory sm;
    private final ByteBuffer scratch =
            ByteBuffer.allocate(Proto.INPUT_ENTRY_BYTES).order(ByteOrder.LITTLE_ENDIAN);
    /** Keys this bridge pressed, so a focus loss releases exactly those and nothing else. */
    private final Set<Integer> heldKeys = new HashSet<>();
    private final Set<Integer> heldButtons = new HashSet<>();

    /** Virtual cursor, in GUI-scaled coordinates, used while a Minecraft screen is open. */
    private double cursorX = -1;
    private double cursorY = -1;
    public long replayed = 0;
    /**
     * Attack / use presses seen this tick. Vanilla handles them against blocks on its own; the
     * bridge additionally offers them to Pandora, which owns everything that is not a block.
     */
    private int pendingAttacks = 0;
    private int pendingInteracts = 0;

    public InputReplayer(SharedMemory sm) {
        this.sm = sm;
    }

    /** Drain the input ring. Returns the number of events replayed this tick. */
    public int drain(MinecraftClient client, ClientPlayerEntity player) {
        SharedMemory.Ring ring = sm.inputRing();
        int n = 0;
        while (ring.pop(scratch)) {
            scratch.position(0);
            int type = scratch.getShort() & 0xFFFF;
            int code = scratch.getShort() & 0xFFFF;
            int value = scratch.getInt();
            int aux = scratch.getInt();
            apply(client, player, type, code, value, aux);
            n++;
            replayed++;
        }
        return n;
    }

    /** Consume the attack presses collected since the last call. */
    public int takeAttacks() {
        int n = pendingAttacks;
        pendingAttacks = 0;
        return n;
    }

    /** Consume the "use" presses collected since the last call. */
    public int takeInteracts() {
        int n = pendingInteracts;
        pendingInteracts = 0;
        return n;
    }

    private void apply(MinecraftClient client, ClientPlayerEntity player,
                       int type, int code, int value, int aux) {
        Screen screen = client.currentScreen;
        if (screen == null && (type == Proto.IN_KEY_DOWN || type == Proto.IN_MOUSE_DOWN)) {
            boolean mouse = type == Proto.IN_MOUSE_DOWN;
            if (matches(client.options.attackKey, code, mouse)) pendingAttacks++;
            if (matches(client.options.useKey, code, mouse)) pendingInteracts++;
        }
        switch (type) {
            case Proto.IN_KEY_DOWN -> {
                heldKeys.add(code);
                if (screen != null) {
                    screen.keyPressed(code, 0, aux);
                } else {
                    setKey(InputUtil.Type.KEYSYM, code, true);
                }
            }
            case Proto.IN_KEY_UP -> {
                heldKeys.remove(code);
                if (screen != null) {
                    screen.keyReleased(code, 0, aux);
                }
                setKey(InputUtil.Type.KEYSYM, code, false);
            }
            case Proto.IN_MOUSE_DOWN -> {
                heldButtons.add(code);
                if (screen != null) {
                    screen.mouseClicked(cursorX(client), cursorY(client), code);
                } else {
                    setKey(InputUtil.Type.MOUSE, code, true);
                }
            }
            case Proto.IN_MOUSE_UP -> {
                heldButtons.remove(code);
                if (screen != null) {
                    screen.mouseReleased(cursorX(client), cursorY(client), code);
                }
                setKey(InputUtil.Type.MOUSE, code, false);
            }
            case Proto.IN_MOUSE_MOVE -> mouseMove(client, player, screen, value, aux);
            case Proto.IN_MOUSE_WHEEL -> {
                double amount = value / 120.0;   // BL2 sends wheel notches in WHEEL_DELTA units
                if (screen != null) {
                    screen.mouseScrolled(cursorX(client), cursorY(client), 0.0, amount);
                } else if (player != null) {
                    player.getInventory().scrollInHotbar(amount);
                }
            }
            case Proto.IN_FOCUS_LOST -> releaseAll();
            default -> { }
        }
    }

    private void mouseMove(MinecraftClient client, ClientPlayerEntity player, Screen screen,
                           int dx, int dy) {
        if (screen != null) {
            // Drive the virtual cursor. Minecraft's own hover highlight follows the real OS
            // cursor, which we cannot move for an unfocused window, but clicks land correctly.
            cursorX = clamp(cursorX(client) + dx, 0, client.getWindow().getScaledWidth());
            cursorY = clamp(cursorY(client) + dy, 0, client.getWindow().getScaledHeight());
            for (int button : heldButtons) {
                screen.mouseDragged(cursorX, cursorY, button, dx, dy);
            }
            return;
        }
        if (player == null || dx == 0 && dy == 0) {
            return;
        }
        // changeLookDirection applies vanilla's 0.15 factor; the BL2 host has already applied
        // the player's Minecraft mouse sensitivity, which it reads back out of McState.
        player.changeLookDirection(dx, dy);
    }

    /** Does this raw key or mouse code drive that key binding, with the player's own mapping? */
    private static boolean matches(KeyBinding binding, int code, boolean mouse) {
        return mouse ? binding.matchesMouse(code) : binding.matchesKey(code, 0);
    }

    private static void setKey(InputUtil.Type type, int code, boolean pressed) {
        InputUtil.Key key = type.createFromCode(code);
        KeyBinding.setKeyPressed(key, pressed);
        if (pressed) {
            // onKeyPressed increments the "times pressed" counter vanilla drains with
            // wasPressed(), which is what makes single-shot actions (jump, use, hotbar) fire.
            KeyBinding.onKeyPressed(key);
        }
    }

    /** Release everything this bridge is holding: BL2 opened a menu or lost the game window. */
    public void releaseAll() {
        for (int code : heldKeys) {
            setKey(InputUtil.Type.KEYSYM, code, false);
        }
        for (int code : heldButtons) {
            setKey(InputUtil.Type.MOUSE, code, false);
        }
        heldKeys.clear();
        heldButtons.clear();
    }

    private double cursorX(MinecraftClient client) {
        if (cursorX < 0) {
            cursorX = client.getWindow().getScaledWidth() / 2.0;
        }
        return cursorX;
    }

    private double cursorY(MinecraftClient client) {
        if (cursorY < 0) {
            cursorY = client.getWindow().getScaledHeight() / 2.0;
        }
        return cursorY;
    }

    private static double clamp(double v, double lo, double hi) {
        return v < lo ? lo : Math.min(v, hi);
    }
}
