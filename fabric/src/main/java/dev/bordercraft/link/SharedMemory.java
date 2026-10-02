// BorderCraft shared memory — file-mapped transport + seqlock + SPSC rings (Java side).
//
// v1 transport maps the same file the BL2 Python mod maps (%LOCALAPPDATA%\BorderCraft\bridge.mm).
// Both processes see each other's writes through the shared page cache. The struct layout is
// defined in protocol/bordercraft_protocol.h; see Proto.java for offsets.
package dev.bordercraft.link;

import java.io.IOException;
import java.io.RandomAccessFile;
import java.lang.invoke.MethodHandles;
import java.lang.invoke.VarHandle;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.channels.FileChannel;
import java.nio.file.Files;
import java.nio.file.Path;

public final class SharedMemory implements AutoCloseable {
    public final ByteBuffer buf; // little-endian, direct
    private final FileChannel channel;

    private SharedMemory(ByteBuffer buf, FileChannel channel) {
        this.buf = buf;
        this.channel = channel;
    }

    /** Create (or take over) the mapping and write the header. BL2 side does this. */
    public static SharedMemory create(Path path) throws IOException {
        Files.createDirectories(path.getParent());
        try (RandomAccessFile f = new RandomAccessFile(path.toFile(), "rw")) {
            f.setLength(Proto.MAPPING_BYTES);
            FileChannel ch = f.getChannel();
            ByteBuffer buf = ch.map(FileChannel.MapMode.READ_WRITE, 0, Proto.MAPPING_BYTES);
            SharedMemory sm = new SharedMemory(buf.order(ByteOrder.LITTLE_ENDIAN), ch);
            sm.writeHeader((int) ProcessHandle.current().pid(), 0);
            return sm;
        }
    }

    /** Open an existing mapping. Minecraft side. */
    public static SharedMemory open(Path path) throws IOException {
        try (RandomAccessFile f = new RandomAccessFile(path.toFile(), "rw")) {
            FileChannel ch = f.getChannel();
            ByteBuffer buf = ch.map(FileChannel.MapMode.READ_WRITE, 0, f.length());
            SharedMemory sm = new SharedMemory(buf.order(ByteOrder.LITTLE_ENDIAN), ch);
            int magic = sm.buf.getInt((int) Proto.OFF_HEADER);
            int version = sm.buf.getInt((int) Proto.OFF_HEADER + 4);
            if (magic != Proto.MAGIC) throw new IOException("bad magic " + Integer.toHexString(magic));
            if (version != Proto.VERSION) throw new IOException("protocol version " + version + " != " + Proto.VERSION);
            return sm;
        }
    }

    public static Path defaultPath() {
        String base = System.getenv("LOCALAPPDATA");
        if (base != null && !base.isEmpty()) {
            return Path.of(base, "BorderCraft", Proto.MAPPING_FILENAME);
        }
        String rt = System.getenv("XDG_RUNTIME_DIR");
        Path dir = (rt != null && !rt.isEmpty())
                ? Path.of(rt, "bordercraft-" + System.getProperty("user.name"))
                : Path.of(System.getProperty("java.io.tmpdir"), "bordercraft-" + System.getProperty("user.name"));
        return dir.resolve(Proto.MAPPING_FILENAME);
    }

    // ---- header -------------------------------------------------------------------------------
    public void writeHeader(int bl2Pid, int mcPid) {
        long now = monotonicMs();
        buf.putInt((int) Proto.OFF_HEADER, Proto.MAGIC);
        buf.putInt((int) Proto.OFF_HEADER + 4, Proto.VERSION);
        if (bl2Pid != 0) {
            buf.putInt((int) Proto.OFF_HEADER + 8, bl2Pid);
            buf.putLong((int) Proto.OFF_HEADER + 16, now);
        }
        if (mcPid != 0) {
            buf.putInt((int) Proto.OFF_HEADER + 12, mcPid);
            buf.putLong((int) Proto.OFF_HEADER + 24, now);
        }
    }

    public void heartbeat(boolean bl2Side) {
        buf.putLong((int) Proto.OFF_HEADER + (bl2Side ? 16 : 24), monotonicMs());
    }

    public long peerHeartbeatMs(boolean bl2Side) {
        return buf.getLong((int) Proto.OFF_HEADER + (bl2Side ? 16 : 24));
    }

    public boolean peerAlive(boolean bl2Side, long timeoutMs) {
        long beat = peerHeartbeatMs(bl2Side);
        return beat != 0 && (monotonicMs() - beat) < timeoutMs;
    }

    public static long monotonicMs() {
        return System.nanoTime() / 1_000_000L;
    }

    // ---- seqlock --------------------------------------------------------------------------------
    public static final class Seqlock {
        private final ByteBuffer buf;
        private final long off;

        public Seqlock(ByteBuffer buf, long off) {
            this.buf = buf;
            this.off = off;
        }

        /** Bump to odd and return the odd marker to embed in the payload's seq field. */
        public int beginWrite() {
            int seq = buf.getInt((int) off) + 1;
            buf.putInt((int) off, seq);
            VarHandle.fullFence();
            return seq;
        }

        public void endWrite(int oddSeq) {
            VarHandle.fullFence();
            buf.putInt((int) off, oddSeq + 1);
        }

        /** Returns the stable even seq, or -1 if a writer is mid-write. */
        public int tryRead() {
            int a = buf.getInt((int) off);
            VarHandle.fullFence();
            return (a & 1) == 0 ? a : -1;
        }

        public boolean readOk(int seqBefore) {
            VarHandle.fullFence();
            return buf.getInt((int) off) == seqBefore && (seqBefore & 1) == 0;
        }
    }

    public Seqlock bl2StateLock() { return new Seqlock(buf, Proto.OFF_BL2_STATE); }
    public Seqlock mcStateLock() { return new Seqlock(buf, Proto.OFF_MC_STATE); }
    public Seqlock actorTableLock() { return new Seqlock(buf, Proto.OFF_ACTOR_TABLE); }

    // ---- SPSC rings ---------------------------------------------------------------------------
    /** Single-producer single-consumer ring of fixed-size records (SkyCraft scheme). */
    public static final class Ring {
        private final ByteBuffer buf;
        private final long headOff, tailOff, dataOff;
        private final int entries, recBytes;

        public Ring(ByteBuffer buf, long base, int entries, int recBytes) {
            this.buf = buf;
            this.headOff = base + 0x00;
            this.tailOff = base + 0x40;
            this.dataOff = base + 0x80;
            this.entries = entries;
            this.recBytes = recBytes;
        }

        public boolean push(ByteBuffer rec) {
            long head = buf.getLong((int) headOff);
            long tail = buf.getLong((int) tailOff);
            if (head - tail >= entries) return false; // full: drop
            int off = (int) (dataOff + (head % entries) * recBytes);
            int pos = rec.position();
            for (int i = 0; i < recBytes; i++) buf.put(off + i, rec.get(pos + i));
            VarHandle.fullFence();
            buf.putLong((int) headOff, head + 1);
            return true;
        }

        public boolean pop(ByteBuffer out) {
            long tail = buf.getLong((int) tailOff);
            long head = buf.getLong((int) headOff);
            if (tail == head) return false;
            int off = (int) (dataOff + (tail % entries) * recBytes);
            for (int i = 0; i < recBytes; i++) out.put(i, buf.get(off + i));
            VarHandle.fullFence();
            buf.putLong((int) tailOff, tail + 1);
            return true;
        }
    }

    public Ring inputRing() { return new Ring(buf, Proto.OFF_INPUT_RING, Proto.INPUT_RING_ENTRIES, Proto.INPUT_ENTRY_BYTES); }
    public Ring collisionRing() { return new Ring(buf, Proto.OFF_COLLISION_RING, Proto.COLLISION_RING_ENTRIES, Proto.COLLISION_REC_BYTES); }
    public Ring eventRing() { return new Ring(buf, Proto.OFF_EVENT_RING, Proto.EVENT_RING_ENTRIES, Proto.EVENT_ENTRY_BYTES); }

    // ---- overlay double buffer ---------------------------------------------------------------
    private static final VarHandle STATE =
            MethodHandles.byteBufferViewVarHandle(int.class, ByteOrder.LITTLE_ENDIAN);

    /** Frame double buffer (SkyCraft scheme): writer publishes into back slot, reader takes newest. */
    public static final class Overlay {
        private final ByteBuffer buf;
        private long back = 0;    // writer-private slot
        private long frames = 0;

        public Overlay(ByteBuffer buf) { this.buf = buf; }

        private int hdr(int slot) { return (int) (Proto.OFF_OVERLAY_SLOT_HDR + slot * 0x40L); }
        private int px(int slot) { return (int) (Proto.OFF_OVERLAY_PIXELS + slot * Proto.OVERLAY_SLOT_BYTES); }

        /** Writer: copy one BGRA frame into the back slot and publish. Returns the frame id. */
        public long publish(int w, int h, ByteBuffer bgra, boolean bottomUp) {
            if (w <= 0 || h <= 0 || w > Proto.MAX_OVERLAY_W || h > Proto.MAX_OVERLAY_H) {
                throw new IllegalArgumentException("bad overlay size " + w + "x" + h);
            }
            int need = w * h * 4;
            if (bgra.remaining() < need) {
                throw new IllegalArgumentException("pixels: " + bgra.remaining() + " < " + need);
            }
            int slot = (int) back;
            frames++;
            int hdr = hdr(slot);
            buf.putInt(hdr, w);
            buf.putInt(hdr + 4, h);
            buf.putInt(hdr + 8, bottomUp ? 1 : 0);
            buf.putInt(hdr + 12, 0);
            int px = px(slot);
            int pos = bgra.position();
            for (int i = 0; i < need; i++) buf.put(px + i, bgra.get(pos + i));
            buf.putLong(hdr + 0x10, frames); // ready marker, written last
            int old = (int) STATE.getAndSet(buf, Proto.OFF_OVERLAY_CTL, slot | Proto.OVERLAY_DIRTY);
            int newBack = old & 0x3;
            back = (newBack != slot && newBack < Proto.OVERLAY_SLOTS) ? newBack : (slot + 1) % Proto.OVERLAY_SLOTS;
            return frames;
        }

        /** Reader: newest unpublished frame into {@code out}; returns frame id, or -1. */
        public long acquire(ByteBuffer out, int[] whFlags) {
            int state = (int) STATE.getVolatile(buf, Proto.OFF_OVERLAY_CTL);
            if ((state & Proto.OVERLAY_DIRTY) == 0) return -1;
            int slot = state & 0x3;
            if (slot >= Proto.OVERLAY_SLOTS) return -1;
            int hdr = hdr(slot);
            long fid = buf.getLong(hdr + 0x10);
            int w = buf.getInt(hdr), h = buf.getInt(hdr + 4), flags = buf.getInt(hdr + 8);
            if (fid == 0 || w == 0 || h == 0) {
                STATE.setVolatile(buf, Proto.OFF_OVERLAY_CTL, state & ~Proto.OVERLAY_DIRTY);
                return -1;
            }
            int need = w * h * 4;
            int px = px(slot);
            for (int i = 0; i < need; i++) out.put(i, buf.get(px + i));
            if (buf.getLong(hdr + 0x10) != fid) return -1; // raced a rewrite
            if ((int) STATE.getVolatile(buf, Proto.OFF_OVERLAY_CTL) == state) {
                STATE.setVolatile(buf, Proto.OFF_OVERLAY_CTL, slot);
            }
            whFlags[0] = w;
            whFlags[1] = h;
            whFlags[2] = flags;
            return fid;
        }
    }

    public Overlay overlay() { return new Overlay(buf); }

    @Override
    public void close() throws IOException {
        channel.close(); // buffer unmaps on GC; fine for our lifetime
    }
}
