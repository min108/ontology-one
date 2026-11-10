"""Real pipe threads with mock Job owners; no Windows kernel claim on Linux."""
import asyncio
import itertools
import os
import threading
import unittest
from unittest.mock import patch

from backend import windows_async_process as adapter


class FakeJobProcess:
    ids = itertools.count(300000)

    def __init__(self):
        self.pid = next(self.ids)
        self._done = threading.Event()
        self._lock = threading.RLock()
        self.returncode = None
        self.terminations = self.closes = self.waits = 0
        read, write = os.pipe()
        self.child_stdin = os.fdopen(read, "rb", buffering=0)
        self.stdin = os.fdopen(write, "wb", buffering=0)
        read, write = os.pipe()
        self.stdout = os.fdopen(read, "rb", buffering=0)
        self.child_stdout = os.fdopen(write, "wb", buffering=0)
        read, write = os.pipe()
        self.stderr = os.fdopen(read, "rb", buffering=0)
        self.child_stderr = os.fdopen(write, "wb", buffering=0)

    def wait(self, timeout=None):
        self.waits += 1
        if not self._done.wait(timeout):
            raise TimeoutError()
        return self.returncode

    def exit(self, code=0):
        with self._lock:
            if self.returncode is not None:
                return
            self.returncode = code
            for stream in (self.child_stdin, self.child_stdout, self.child_stderr):
                stream.close()
            self._done.set()

    def terminate_tree(self):
        self.terminations += 1
        self.exit(125)

    def close(self):
        self.closes += 1
        for stream in (self.stdin, self.stdout, self.stderr):
            stream.close()


class PartialWriter:
    def __init__(self, stream):
        self.stream = stream

    def write(self, data):
        return self.stream.write(data[:2])

    def flush(self):
        return self.stream.flush()

    def close(self):
        return self.stream.close()


class WindowsAsyncProcessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owners = []

    def process(self, **limits):
        core = FakeJobProcess()
        process = adapter.AsyncWindowsProcess(core, **limits)
        self.owners.append((core, process))
        return core, process

    async def asyncTearDown(self):
        for core, process in self.owners:
            await process.aclose()
            self.assertFalse([thread.name for thread in process._threads if thread.is_alive()])
            self.assertEqual(core.closes, 1)

    async def test_bidirectional_ready_and_finalize_handshake(self):
        core, process = self.process()
        core.child_stdout.write(b'{"phase":"ready"}\n')
        self.assertEqual(await process.stdout.readline(), b'{"phase":"ready"}\n')
        process.stdin.write(b'{"phase":"finalize"}\n')
        await process.stdin.drain()
        self.assertEqual(core.child_stdin.readline(), b'{"phase":"finalize"}\n')
        core.child_stdout.write(b'{"ok":true}\n')
        self.assertEqual(await process.stdout.readline(), b'{"ok":true}\n')
        core.exit()
        self.assertEqual(await process.wait(), 0)
        self.assertEqual(await process.stdout.read(), b'')

    async def test_cancelling_read_does_not_create_or_lose_reader_thread(self):
        core, process = self.process()
        threads = tuple(process._threads)
        waiting = asyncio.create_task(process.stdout.readline())
        await asyncio.sleep(0)
        waiting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiting
        core.child_stdout.write(b'after-cancel\n')
        self.assertEqual(await process.stdout.readline(), b'after-cancel\n')
        self.assertEqual(tuple(process._threads), threads)

    async def test_output_limit_kills_job_and_does_not_echo_output(self):
        core, process = self.process(max_stdout=8)
        core.child_stdout.write(b'secret-value-exceeds-limit')
        with self.assertRaises(adapter.WindowsProcessIOError) as caught:
            await process.stdout.read()
        self.assertEqual(str(caught.exception), 'WINDOWS_PROCESS_OUTPUT_LIMIT')
        self.assertEqual(core.terminations, 1)

    async def test_total_output_limit_survives_consuming_previous_chunks(self):
        core, process = self.process(max_stdout=8)
        core.child_stdout.write(b'1234')
        self.assertEqual(await process.stdout.read(4), b'1234')
        core.child_stdout.write(b'56789')
        with self.assertRaises(adapter.WindowsProcessIOError):
            await process.stdout.read(5)

    async def test_line_limit_tracks_a_line_across_chunks_and_reads(self):
        core, process = self.process(max_stdout=32, max_line_bytes=6)
        core.child_stdout.write(b'1234')
        self.assertEqual(await process.stdout.read(4), b'1234')
        core.child_stdout.write(b'567\n')
        with self.assertRaises(adapter.WindowsProcessIOError):
            await process.stdout.readline()

    async def test_separate_stderr_limit_notifies_stdout_waiter(self):
        core, process = self.process(max_stderr=4)
        waiting = asyncio.create_task(process.stdout.readline())
        core.child_stderr.write(b'12345')
        with self.assertRaises(adapter.WindowsProcessIOError):
            await waiting

    async def test_partial_writes_are_completed_before_drain(self):
        core = FakeJobProcess()
        core.stdin = PartialWriter(core.stdin)
        process = adapter.AsyncWindowsProcess(core)
        self.owners.append((core, process))
        process.stdin.write(b'partial-write\n')
        await process.stdin.drain()
        self.assertEqual(core.child_stdin.readline(), b'partial-write\n')
        process.stdin.close()
        await process.stdin.wait_closed()
        self.assertEqual(core.child_stdin.read(1), b'')

    async def test_input_limit_kills_job_without_queuing_excess(self):
        core, process = self.process(max_stdin=4)
        with self.assertRaises(adapter.WindowsProcessIOError) as caught:
            process.stdin.write(b'12345')
        self.assertEqual(str(caught.exception), 'WINDOWS_PROCESS_INPUT_LIMIT')
        self.assertEqual(core.terminations, 1)
        self.assertEqual(process.stdin.total, 0)

    async def test_cancel_blocked_stdin_drain_then_close_reaps_writer(self):
        core, process = self.process()
        process.stdin.write(b'x' * (512 * 1024))
        waiting = asyncio.create_task(process.stdin.drain())
        await asyncio.sleep(.01)
        self.assertFalse(waiting.done())
        waiting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiting
        await process.aclose()
        self.assertFalse(process.stdin.thread.is_alive())

    async def test_cancel_wait_reuses_the_single_native_waiter(self):
        core, process = self.process()
        waiting = asyncio.create_task(process.wait())
        await asyncio.sleep(0)
        waiting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiting
        core.exit(7)
        self.assertEqual(await process.wait(), 7)
        self.assertEqual(core.waits, 1)

    async def test_close_is_idempotent_and_does_not_kill_sibling(self):
        first, process = self.process()
        second, sibling = self.process()
        await process.aclose()
        await process.aclose()
        self.assertEqual(first.terminations, 1)
        self.assertIsNone(second.returncode)
        second.child_stdout.write(b'sibling\n')
        self.assertEqual(await sibling.stdout.readline(), b'sibling\n')

    async def test_cancel_during_native_spawn_waits_for_owner_and_cleans_it(self):
        entered, release = threading.Event(), threading.Event()
        core = FakeJobProcess()

        def delayed(*args, **kwargs):
            entered.set()
            release.wait(3)
            return core

        with patch.object(adapter, '_spawn', side_effect=delayed):
            task = asyncio.create_task(adapter.async_spawn(['trusted.exe'], cwd='.', env={}))
            await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(core.closes, 1)
        self.assertEqual(core.terminations, 1)
        self.assertFalse([thread for thread in threading.enumerate() if thread.name.endswith(str(core.pid))])

    async def test_repeated_cancellation_during_close_still_finishes_cleanup(self):
        core, process = self.process()
        entered, release = threading.Event(), threading.Event()
        original = core.terminate_tree

        def delayed():
            entered.set()
            release.wait(3)
            original()

        with patch.object(core, 'terminate_tree', side_effect=delayed):
            task = asyncio.create_task(process.aclose())
            await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(core.closes, 1)
        self.assertFalse(any(thread.is_alive() for thread in process._threads))

    async def test_invalid_limits_fail_before_native_spawn(self):
        for options in ({'max_stdout': 0}, {'max_stderr': True},
                        {'max_line_bytes': 0}, {'max_stdin': 65 * 1024 * 1024}):
            with self.subTest(options=options), patch.object(adapter, '_spawn') as spawn:
                with self.assertRaises(ValueError):
                    await adapter.async_spawn(['trusted.exe'], cwd='.', env={}, **options)
                spawn.assert_not_called()

    async def test_pipe_notifications_are_coalesced_until_the_loop_handles_them(self):
        _, process = self.process()
        event = asyncio.Event()
        with patch.object(process._loop, 'call_soon_threadsafe') as notify:
            for _ in range(1000):
                process._notify(event)
            self.assertEqual(notify.call_count, 1)
            notify.call_args.args[0]()
            self.assertTrue(event.is_set())
            process._notify(event)
            self.assertEqual(notify.call_count, 2)
            notify.call_args.args[0]()

    async def test_failed_termination_still_closes_native_kill_on_close_owner(self):
        core = FakeJobProcess()
        process = adapter.AsyncWindowsProcess(core)
        original_close = core.close

        def kill_on_close():
            core.exit(125)
            original_close()

        with patch.object(core, 'terminate_tree', side_effect=OSError('private native error')), \
                patch.object(core, 'close', side_effect=kill_on_close), \
                patch.object(adapter, '_JOIN_SECONDS', .02):
            with self.assertRaises(adapter.WindowsProcessIOError) as caught:
                await process.aclose()
        self.assertEqual(str(caught.exception), 'WINDOWS_PROCESS_CLEANUP_FAILED')
        self.assertEqual(core.closes, 1)
        self.assertFalse(any(thread.is_alive() for thread in process._threads))


if __name__ == '__main__':
    unittest.main()
