// BorderCraft - a stable RGB for any item, computed without touching the GPU.
//
// Borderlands 2 cannot read Minecraft's item atlas: the overlay is drawn by a software
// rasterizer in Python, so every item has to arrive as a colour plus a name. Blocks use their
// map colour (the same palette Minecraft itself uses for maps, so dirt is brown and diamond ore
// is grey-cyan), and everything else is derived from rarity and a hash of the item id, which is
// stable across sessions and across worlds.
package dev.bordercraft.bridge;

import net.minecraft.block.Block;
import net.minecraft.block.MapColor;
import net.minecraft.item.BlockItem;
import net.minecraft.item.Item;
import net.minecraft.item.ItemStack;
import net.minecraft.registry.Registries;
import net.minecraft.util.Identifier;
import net.minecraft.util.Rarity;

import java.util.HashMap;
import java.util.Map;

public final class ItemColors {
    private ItemColors() {}

    private static final Map<Item, Integer> CACHE = new HashMap<>();

    /** 0x00RRGGBB for an item stack, cached per item. */
    public static int rgb(ItemStack stack) {
        if (stack.isEmpty()) {
            return 0;
        }
        Item item = stack.getItem();
        Integer cached = CACHE.get(item);
        if (cached != null) {
            return cached;
        }
        int value = compute(stack, item);
        CACHE.put(item, value);
        return value;
    }

    private static int compute(ItemStack stack, Item item) {
        if (item instanceof BlockItem blockItem) {
            Block block = blockItem.getBlock();
            try {
                // getDefaultMapColor() reads the block settings only: no world, no BlockPos, so
                // it is safe to call off the render thread and before any chunk is loaded.
                MapColor mapColor = block.getDefaultMapColor();
                if (mapColor != null && mapColor != MapColor.CLEAR) {
                    return brighten(mapColor.color);
                }
            } catch (Throwable ignored) {
                // A modded block with an exotic settings object: fall through to the hash.
            }
        }
        return tint(hash(item), stack.getRarity());
    }

    /** A stable 32-bit identity for an item, so BL2 can cache the sprite it drew for it. */
    public static int hash(Item item) {
        Identifier id = Registries.ITEM.getId(item);
        int h = 0x811C9DC5;
        for (int i = 0; i < id.toString().length(); i++) {
            h = (h ^ id.toString().charAt(i)) * 0x01000193;
        }
        return h;
    }

    /** Map colours are mid-tone; the overlay is drawn over a dark panel, so lift them. */
    private static int brighten(int color) {
        int r = Math.min(255, ((color >> 16) & 0xFF) * 5 / 4);
        int g = Math.min(255, ((color >> 8) & 0xFF) * 5 / 4);
        int b = Math.min(255, (color & 0xFF) * 5 / 4);
        return (r << 16) | (g << 8) | b;
    }

    /** Spread the hash over a readable band of the colour wheel, biased by rarity. */
    private static int tint(int hash, Rarity rarity) {
        float hue = ((hash >>> 8) & 0xFFFF) / 65535.0f;
        float saturation = switch (rarity) {
            case COMMON -> 0.28f;
            case UNCOMMON -> 0.55f;
            case RARE -> 0.70f;
            case EPIC -> 0.85f;
        };
        float value = switch (rarity) {
            case COMMON -> 0.72f;
            case UNCOMMON -> 0.85f;
            default -> 0.95f;
        };
        return hsvToRgb(hue, saturation, value);
    }

    private static int hsvToRgb(float h, float s, float v) {
        int sector = (int) (h * 6.0f) % 6;
        float f = h * 6.0f - (float) Math.floor(h * 6.0f);
        float p = v * (1 - s);
        float q = v * (1 - f * s);
        float t = v * (1 - (1 - f) * s);
        float r, g, b;
        switch (sector) {
            case 0 -> { r = v; g = t; b = p; }
            case 1 -> { r = q; g = v; b = p; }
            case 2 -> { r = p; g = v; b = t; }
            case 3 -> { r = p; g = q; b = v; }
            case 4 -> { r = t; g = p; b = v; }
            default -> { r = v; g = p; b = q; }
        }
        return (Math.round(r * 255) << 16) | (Math.round(g * 255) << 8) | Math.round(b * 255);
    }
}
