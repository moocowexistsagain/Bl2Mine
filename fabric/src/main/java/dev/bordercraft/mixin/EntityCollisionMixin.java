// BorderCraft - the one place Minecraft's own code is touched.
//
// Minecraft resolves a move by asking for every collision shape along the path and then running
// its solver. We append Borderlands 2's shapes to that list and change nothing else: gravity,
// step-up, sprint jumping, sneaking at an edge and swim physics all stay exactly as Mojang
// wrote them, which is the entire point of the project.
package dev.bordercraft.mixin;

import dev.bordercraft.bridge.CollisionField;
import net.minecraft.entity.Entity;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Vec3d;
import net.minecraft.util.shape.VoxelShape;
import net.minecraft.world.World;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.ModifyVariable;

import java.util.ArrayList;
import java.util.List;

@Mixin(Entity.class)
public abstract class EntityCollisionMixin {
    @ModifyVariable(
            method = "adjustMovementForCollisions(Lnet/minecraft/entity/Entity;Lnet/minecraft/util/math/Vec3d;"
                    + "Lnet/minecraft/util/math/Box;Lnet/minecraft/world/World;Ljava/util/List;)"
                    + "Lnet/minecraft/util/math/Vec3d;",
            at = @At("HEAD"),
            argsOnly = true,
            index = 4,
            require = 0)
    private static List<VoxelShape> bordercraft$addPandoraCollision(
            List<VoxelShape> collisions, Entity entity, Vec3d movement, Box box, World world) {
        CollisionField field = CollisionField.INSTANCE;
        if (!field.isEnabled()) {
            return collisions;
        }
        // The swept volume, so a fast move cannot tunnel through Pandora.
        Box swept = box.stretch(movement).expand(1.0, 1.0, 1.0);
        List<VoxelShape> extra = field.collect(swept);
        if (extra.isEmpty()) {
            return collisions;
        }
        List<VoxelShape> merged = new ArrayList<>(collisions.size() + extra.size());
        merged.addAll(collisions);
        merged.addAll(extra);
        return merged;
    }
}
