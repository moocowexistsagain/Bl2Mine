#!/usr/bin/env python3
"""Regression tests for bridge creation, including Windows' mapped-file restrictions.

Run with: python3 tools/test_bridge_lifecycle.py
Uses only the standard library; no games or SDK required. The Windows guard also reproduces
ERROR_USER_MAPPED_FILE / EINVAL on non-Windows hosts so this failure cannot go unnoticed there.
"""
from __future__ import annotations

import builtins
import errno
import os
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "protocol", "python"))
import bordercraft_protocol as P  # noqa: E402


class WindowsMappedFileGuard:
    """Allow opening a mapped file, but reject truncating opens and all resize attempts."""

    def __init__(self, path: str):
        self.path = path
        self.modes: list[str] = []
        self.resize_attempts: list[int | None] = []

    def open(self, path, mode="r", *args, **kwargs):
        self.modes.append(mode)
        if "w" in mode:
            raise OSError(errno.EINVAL, "Invalid argument", path)
        file = builtins.open(path, mode, *args, **kwargs)
        return self.File(file, self)

    class File:
        def __init__(self, file, guard):
            self.file = file
            self.guard = guard

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.file.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.file, name)

        def truncate(self, size=None):
            self.guard.resize_attempts.append(size)
            raise OSError(errno.EINVAL, "Invalid argument", self.guard.path)


class BridgeLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="bordercraft-lifecycle-")
        self.addCleanup(temporary.cleanup)
        # Exercise spaces and Unicode in a normal filesystem path, too.
        self.path = os.path.join(temporary.name, "BorderCraft test \u00e9", P.MAPPING_FILENAME)

    def create(self, **kwargs):
        bridge = P.Bridge.create(self.path, **kwargs)
        self.addCleanup(bridge.close)
        return bridge

    def write_stale_file(self, size, fill=None):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with builtins.open(self.path, "wb") as file:
            if fill is not None:
                file.write(fill * size)
            else:
                file.truncate(size)

    def assert_initialized(self, bridge):
        self.assertEqual(os.path.getsize(self.path), P.MAPPING_BYTES)
        self.assertEqual(len(bridge.buf), P.MAPPING_BYTES)
        header = bridge.read_header()
        self.assertEqual((header.magic, header.version), (P.MAGIC, P.VERSION))
        self.assertEqual(header.bl2_pid, os.getpid())
        self.assertGreater(header.bl2_heartbeat_ms, 0)
        self.assertEqual((header.mc_pid, header.mc_heartbeat_ms), (0, 0))
        self.assertFalse(any(bridge.buf[struct.calcsize(P._HDR_FMT):]), "stale bridge data survived")

    def test_creates_full_initialized_bridge(self):
        self.assert_initialized(self.create(retries=1))

    def test_reuses_bridge_while_peer_still_maps_it(self):
        host = self.create(retries=1)
        peer = P.Bridge.open(self.path)
        self.addCleanup(peer.close)
        host.write_bl2_state(P.Bl2State(world_id=42, flags=P.BL2_IN_GAME))
        host.push_input(P.InputEntry(type=P.IN_KEY_DOWN, code=87))
        peer.write_header(mc_pid=12345)
        peer.write_mc_state(P.McState(flags=P.MC_IN_WORLD, x=12.0))
        peer.push_event(P.McEvent(type=P.EVT_BLOCK_PLACE))
        peer.overlay.publish(1, 1, b"\x01\x02\x03\xff")
        host.buf[-4:] = b"tail"
        host.close()  # Minecraft's mapping remains open when BL2 is re-enabled.

        guard = WindowsMappedFileGuard(self.path)
        with mock.patch.object(P, "open", side_effect=guard.open, create=True):
            restarted = self.create(retries=1)

        self.assertEqual(guard.modes, ["r+b"])
        self.assertEqual(guard.resize_attempts, [])
        self.assert_initialized(restarted)
        self.assertIsNone(peer.pop_input())
        self.assertIsNone(restarted.pop_event())
        self.assertIsNone(restarted.overlay.acquire())
        self.assertEqual(peer.read_mc_state().flags, 0)
        self.assertEqual(peer.buf[-4:], b"\x00" * 4)
        # The peer must still see the SAME file, not an unlinked/replaced mapping.
        restarted.write_bl2_state(P.Bl2State(world_id=99))
        self.assertEqual(peer.read_bl2_state().world_id, 99)

    def test_fully_resets_correctly_sized_stale_file_without_truncating(self):
        self.write_stale_file(P.MAPPING_BYTES, b"\xa5")
        guard = WindowsMappedFileGuard(self.path)
        with mock.patch.object(P, "open", side_effect=guard.open, create=True):
            bridge = self.create(retries=1)
        self.assertEqual(guard.resize_attempts, [])
        self.assert_initialized(bridge)

    def test_resizes_unmapped_stale_files(self):
        for size in (0, 1024, P.MAPPING_BYTES + 4096):
            with self.subTest(size=size):
                self.write_stale_file(size)
                bridge = self.create(retries=1)
                self.assert_initialized(bridge)
                bridge.close()

    def test_wrong_sized_mapped_file_reports_resize_failure_without_deleting(self):
        self.write_stale_file(1024, b"\xa5")
        guard = WindowsMappedFileGuard(self.path)
        with mock.patch.object(P, "open", side_effect=guard.open, create=True), \
                mock.patch.object(P.time, "sleep") as sleep, \
                mock.patch.object(P.os, "remove") as remove:
            with self.assertRaises(RuntimeError) as caught:
                P.Bridge.create(self.path, retries=2)

        message = str(caught.exception)
        self.assertIn("resizing", message)
        self.assertIn("1024", message)
        self.assertIn(str(P.MAPPING_BYTES), message)
        self.assertIn(self.path, message)
        self.assertIn("Close both games", message)
        self.assertEqual(caught.exception.__cause__.errno, errno.EINVAL)
        self.assertEqual(guard.modes, ["r+b", "r+b"])
        self.assertEqual(guard.resize_attempts, [P.MAPPING_BYTES, P.MAPPING_BYTES])
        sleep.assert_called_once_with(0.1)
        remove.assert_not_called()
        with builtins.open(self.path, "rb") as file:
            self.assertEqual(file.read(), b"\xa5" * 1024)

    def test_retries_transient_open_failure(self):
        calls = 0

        def transient_open(path, mode):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise PermissionError(errno.EACCES, "temporarily locked", path)
            return builtins.open(path, mode)

        with mock.patch.object(P, "open", side_effect=transient_open, create=True), \
                mock.patch.object(P.time, "sleep") as sleep:
            bridge = self.create(retries=2)
        sleep.assert_called_once_with(0.1)
        self.assert_initialized(bridge)

    def test_reopens_file_if_it_appears_during_creation(self):
        modes = []

        def racing_open(path, mode):
            modes.append(mode)
            if mode == "x+b":
                self.write_stale_file(P.MAPPING_BYTES, b"\xa5")
            return builtins.open(path, mode)

        with mock.patch.object(P, "open", side_effect=racing_open, create=True), \
                mock.patch.object(P.time, "sleep") as sleep:
            bridge = self.create(retries=2)
        self.assertEqual(modes, ["r+b", "x+b", "r+b"])
        sleep.assert_called_once_with(0.1)
        self.assert_initialized(bridge)

    def test_open_error_reports_operation_and_preserves_cause(self):
        error = OSError(errno.EINVAL, "Invalid argument", self.path)
        with mock.patch.object(P, "open", side_effect=error, create=True), \
                mock.patch.object(P.time, "sleep") as sleep:
            with self.assertRaises(RuntimeError) as caught:
                P.Bridge.create(self.path, retries=1)
        self.assertIn("opening", str(caught.exception))
        self.assertIn(self.path, str(caught.exception))
        self.assertIs(caught.exception.__cause__, error)
        sleep.assert_not_called()

    def test_directory_error_reports_operation_and_preserves_cause(self):
        error = PermissionError(errno.EACCES, "Access denied", os.path.dirname(self.path))
        with mock.patch.object(P.os, "makedirs", side_effect=error), \
                mock.patch.object(P.time, "sleep"):
            with self.assertRaises(RuntimeError) as caught:
                P.Bridge.create(self.path, retries=1)
        self.assertIn("directory", str(caught.exception))
        self.assertIn(self.path, str(caught.exception))
        self.assertIs(caught.exception.__cause__, error)

    def test_mapping_failure_closes_file(self):
        files = []
        error = OSError(errno.EINVAL, "mapping failed")

        def tracked_open(path, mode):
            file = builtins.open(path, mode)
            self.addCleanup(file.close)
            files.append(file)
            return file

        with mock.patch.object(P, "open", side_effect=tracked_open, create=True), \
                mock.patch.object(P.mmap, "mmap", side_effect=error):
            with self.assertRaises(RuntimeError) as caught:
                P.Bridge.create(self.path, retries=1)
        self.assertIn("mapping", str(caught.exception))
        self.assertIs(caught.exception.__cause__, error)
        self.assertTrue(files)
        self.assertTrue(all(file.closed for file in files))

    def test_initialization_failure_closes_mapping(self):
        mappings = []
        real_mmap = P.mmap.mmap

        def tracked_mmap(*args, **kwargs):
            buf = real_mmap(*args, **kwargs)
            self.addCleanup(buf.close)
            mappings.append(buf)
            return buf

        class BrokenBridge(P.Bridge):
            def __init__(self, buf, path):
                raise RuntimeError("initialization failed")

        with mock.patch.object(P.mmap, "mmap", side_effect=tracked_mmap):
            with self.assertRaises(RuntimeError):
                BrokenBridge.create(self.path, retries=1)
        self.assertTrue(mappings)
        self.assertTrue(all(buf.closed for buf in mappings))

    def test_magic_is_published_after_reset_and_header_metadata(self):
        self.write_stale_file(P.MAPPING_BYTES, b"\xa5")
        pack_into = struct.pack_into
        publications = []

        def checked_pack(fmt, buf, offset, *values):
            if fmt == "<I" and offset == P.OFF_HEADER and values == (P.MAGIC,):
                self.assertEqual(struct.unpack_from("<I", buf, P.OFF_HEADER)[0], 0)
                header = struct.unpack_from(P._HDR_FMT, buf, P.OFF_HEADER)
                self.assertEqual(header[1:4], (P.VERSION, os.getpid(), 0))
                self.assertGreater(header[4], 0)
                self.assertFalse(any(buf[struct.calcsize(P._HDR_FMT):]))
                publications.append(True)
            return pack_into(fmt, buf, offset, *values)

        with mock.patch.object(P.struct, "pack_into", side_effect=checked_pack):
            self.create(retries=1)
        self.assertEqual(publications, [True])

    def test_rejects_nonpositive_retry_count(self):
        for retries in (0, -1):
            with self.subTest(retries=retries):
                with self.assertRaisesRegex(ValueError, "retries"):
                    P.Bridge.create(self.path, retries=retries)


if __name__ == "__main__":
    unittest.main(verbosity=2)
