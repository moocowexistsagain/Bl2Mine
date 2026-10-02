// BorderCraft - the Minecraft HUD and the full inventory, shipped as data.
//
// The whole inventory crosses the bridge every tick: hearts, hunger, armour, air, XP, the
// hotbar, the 27 main slots, the four armour slots and the offhand. Borderlands 2 draws it with
// Canvas primitives, so each slot travels as a colour, a count, a damage fraction and a name
// rather than as a texture. Hashes let the BL2 side cache the tile it drew for an item.
package dev.bordercraft.bridge;

import dev.bordercraft.link.Proto;
import dev.bordercraft.link.SharedMemory;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.item.BlockItem;
import net.minecraft.item.ItemStack;

import java.nio.charset.StandardCharsets;
import java.util.Arrays;

public final class HudPublisher {
    /** 0-8 hotbar, 9-35 main, 36-39 armour, 40 offhand: PlayerInventory's combined indexing. */
    private static final int SLOT_COUNT = 41;

    private final SharedMemory sm;
    private final byte[] nameScratch = new byte[Proto.HUD_NAME_BYTES];

    public HudPublisher(SharedMemory sm) {
        this.sm = sm;
    }

    public void publish(MinecraftClient client, ClientPlayerEntity player) {
        int flags = 0;
        if (client.currentScreen != null) flags |= Proto.HUD_SCREEN_OPEN;
        if (client.currentScreen instanceof net.minecraft.client.gui.screen.ingame.InventoryScreen
                || client.currentScreen instanceof net.minecraft.client.gui.screen.ingame.CreativeInventoryScreen) {
            flags |= Proto.HUD_INVENTORY_OPEN;
        }
        if (player.isCreative()) flags |= Proto.HUD_CREATIVE;
        if (player.getWorld().getLevelProperties().isHardcore()) flags |= Proto.HUD_HARDCORE;
        if (player.isSubmergedInWater()) flags |= Proto.HUD_UNDERWATER;

        SharedMemory.Seqlock lock = sm.hudLock();
        int seq = lock.beginWrite();
        int off = (int) Proto.OFF_HUD;
        sm.buf.putInt(off, seq);
        sm.buf.putInt(off + (int) Proto.HUD_OFF_FLAGS, flags);
        sm.buf.putFloat(off + (int) Proto.HUD_OFF_HEALTH, player.getHealth());
        sm.buf.putFloat(off + (int) Proto.HUD_OFF_MAX_HEALTH, player.getMaxHealth());
        sm.buf.putFloat(off + (int) Proto.HUD_OFF_ABSORPTION, player.getAbsorptionAmount());
        sm.buf.putFloat(off + (int) Proto.HUD_OFF_ARMOR, player.getArmor());
        sm.buf.putFloat(off + (int) Proto.HUD_OFF_FOOD, player.getHungerManager().getFoodLevel());
        sm.buf.putFloat(off + (int) Proto.HUD_OFF_SATURATION, player.getHungerManager().getSaturationLevel());
        sm.buf.putFloat(off + (int) Proto.HUD_OFF_AIR, player.getAir());
        sm.buf.putFloat(off + (int) Proto.HUD_OFF_MAX_AIR, player.getMaxAir());
        sm.buf.putInt(off + (int) Proto.HUD_OFF_XP_LEVEL, player.experienceLevel);
        sm.buf.putFloat(off + (int) Proto.HUD_OFF_XP_PROGRESS, player.experienceProgress);
        int selected = player.getInventory().selectedSlot;
        sm.buf.putInt(off + (int) Proto.HUD_OFF_SELECTED, selected);
        sm.buf.putInt(off + (int) Proto.HUD_OFF_SLOT_COUNT, SLOT_COUNT);

        for (int i = 0; i < SLOT_COUNT; i++) {
            writeSlot(off + (int) Proto.HUD_OFF_SLOTS + i * Proto.HUD_SLOT_BYTES,
                    player.getInventory().getStack(i), i == selected);
        }
        lock.endWrite(seq);
    }

    private void writeSlot(int base, ItemStack stack, boolean selected) {
        int slotFlags = selected ? Proto.SLOT_SELECTED : 0;
        if (stack.isEmpty()) {
            sm.buf.putInt(base + (int) Proto.SLOT_OFF_HASH, 0);
            sm.buf.putShort(base + (int) Proto.SLOT_OFF_COUNT, (short) 0);
            sm.buf.putShort(base + (int) Proto.SLOT_OFF_FLAGS, (short) slotFlags);
            sm.buf.putInt(base + (int) Proto.SLOT_OFF_RGB, 0);
            sm.buf.putShort(base + (int) Proto.SLOT_OFF_DAMAGE, (short) 0);
            sm.buf.put(base + (int) Proto.SLOT_OFF_NAME, new byte[Proto.HUD_NAME_BYTES], 0,
                    Proto.HUD_NAME_BYTES);
            return;
        }
        if (stack.getItem() instanceof BlockItem) slotFlags |= Proto.SLOT_BLOCK;
        if (stack.hasGlint()) slotFlags |= Proto.SLOT_ENCHANTED;
        // damage travels as a 0..10000 fraction: the bar is drawn by BL2, so it needs no maxima.
        int damage = 0;
        if (stack.isDamageable() && stack.isDamaged()) {
            slotFlags |= Proto.SLOT_DAMAGED;
            damage = Math.round(10000.0f * stack.getDamage() / Math.max(1, stack.getMaxDamage()));
        }
        sm.buf.putInt(base + (int) Proto.SLOT_OFF_HASH, ItemColors.hash(stack.getItem()));
        sm.buf.putShort(base + (int) Proto.SLOT_OFF_COUNT, (short) Math.min(0x7FFF, stack.getCount()));
        sm.buf.putShort(base + (int) Proto.SLOT_OFF_FLAGS, (short) slotFlags);
        sm.buf.putInt(base + (int) Proto.SLOT_OFF_RGB, ItemColors.rgb(stack));
        sm.buf.putShort(base + (int) Proto.SLOT_OFF_DAMAGE, (short) Math.min(10000, damage));
        putName(base + (int) Proto.SLOT_OFF_NAME, stack.getName().getString());
    }

    /** UTF-8, truncated on a byte boundary and zero padded: BL2 reads up to the first NUL. */
    private void putName(int base, String name) {
        Arrays.fill(nameScratch, (byte) 0);
        byte[] raw = name.getBytes(StandardCharsets.UTF_8);
        int n = Math.min(raw.length, Proto.HUD_NAME_BYTES - 1);
        while (n > 0 && (raw[n] & 0xC0) == 0x80) {
            n--;   // never split a multi-byte sequence
        }
        System.arraycopy(raw, 0, nameScratch, 0, n);
        sm.buf.put(base, nameScratch, 0, Proto.HUD_NAME_BYTES);
    }
}
