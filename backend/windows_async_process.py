"""Bounded asynchronous pipes for processes owned by a Windows Job Object.

The synchronous owner creates the security boundary. This adapter never starts
an ordinary subprocess and never falls back when creation or containment fails.
One thread owns each pipe for its entire lifetime; cancelling an async read does
not create another blocking reader. Limits apply to total bytes for a session,
so neither a slow consumer nor ``read(-1)`` can accumulate unbounded output.
"""
from __future__ import annotations

import asyncio
from collections import deque
import subprocess
import threading
import time


PIPE = subprocess.PIPE
_MAX_LIMIT = 64 * 1024 * 1024
_JOIN_SECONDS = 5


class WindowsProcessIOError(RuntimeError):
    """A fixed public error code, without child output or native error text."""

    def __init__(self, code="WINDOWS_PROCESS_IO_FAILED"):
        self.code = code
        super().__init__(code)


def _limit(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _MAX_LIMIT:
        raise ValueError(f"Invalid {name} limit")
    return value


async def _finish(task):
    """Finish an owned cleanup even if the caller receives another cancel."""
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                return task.result()


class _Reader:
    def __init__(self, owner, stream, limit, line_limit):
        self.owner, self.stream = owner, stream
        self.limit, self.line_limit = limit, line_limit
        self.buffer, self.total, self.unfinished_line = bytearray(), 0, 0
        self.eof = False
        self.changed = asyncio.Event()
        self.read_lock = asyncio.Lock()
        self.thread = owner._thread(self._pump, "reader")

    def _pump(self):
        try:
            while True:
                # The native owner supplies unbuffered binary FileIO pipes.
                chunk = self.stream.read(min(65536, self.limit + 1))
                if not chunk:
                    break
                failed = False
                with self.owner._lock:
                    self.total += len(chunk)
                    if self.total > self.limit or len(self.buffer) + len(chunk) > self.limit:
                        failed = True
                    else:
                        pieces = chunk.split(b"\n")
                        for index, piece in enumerate(pieces):
                            self.unfinished_line += len(piece)
                            if index < len(pieces) - 1:
                                self.unfinished_line += 1
                            if self.unfinished_line > self.line_limit:
                                failed = True
                                break
                            if index < len(pieces) - 1:
                                self.unfinished_line = 0
                        if not failed:
                            self.buffer.extend(chunk)
                self.owner._notify(self.changed)
                if failed:
                    self.owner._fail("WINDOWS_PROCESS_OUTPUT_LIMIT")
                    break
        except (OSError, ValueError, TypeError):
            if not self.owner._closing:
                self.owner._fail("WINDOWS_PROCESS_IO_FAILED")
        finally:
            with self.owner._lock:
                self.eof = True
            self.owner._notify(self.changed)

    async def read(self, n=-1):
        if not isinstance(n, int):
            raise TypeError("read size must be an integer")
        if n == 0:
            return b""
        result = bytearray()
        async with self.read_lock:
            while True:
                with self.owner._lock:
                    self.owner._raise_error()
                    if self.buffer:
                        amount = len(self.buffer) if n < 0 else min(n, len(self.buffer))
                        result.extend(self.buffer[:amount])
                        del self.buffer[:amount]
                        if n >= 0:
                            return bytes(result)
                    if self.eof:
                        return bytes(result)
                    self.changed.clear()
                await self.changed.wait()

    async def readline(self):
        async with self.read_lock:
            while True:
                with self.owner._lock:
                    self.owner._raise_error()
                    end = self.buffer.find(b"\n")
                    if end >= 0:
                        result = bytes(self.buffer[:end + 1])
                        del self.buffer[:end + 1]
                        return result
                    if self.eof:
                        result = bytes(self.buffer)
                        self.buffer.clear()
                        return result
                    self.changed.clear()
                await self.changed.wait()

    def at_eof(self):
        with self.owner._lock:
            return self.eof and not self.buffer


class _Writer:
    def __init__(self, owner, stream, limit):
        self.owner, self.stream, self.limit = owner, stream, limit
        self.queue = deque()
        self.pending_bytes = self.total = self.submitted = self.completed = 0
        self.closed = self.aborted = self.finished = False
        self.condition = threading.Condition(owner._lock)
        self.changed = asyncio.Event()
        self.thread = owner._thread(self._pump, "writer")

    def write(self, data):
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("stdin accepts bytes only")
        length = data.nbytes if isinstance(data, memoryview) else len(data)
        with self.owner._lock:
            self.owner._raise_error()
            if self.closed or self.owner._closing:
                raise WindowsProcessIOError("WINDOWS_PROCESS_STDIN_CLOSED")
            if self.total + length > self.limit or self.pending_bytes + length > self.limit:
                failed = True
            else:
                failed = False
                if length:
                    data = bytes(data)
                    self.total += length
                    self.pending_bytes += length
                    self.submitted += 1
                    self.queue.append((self.submitted, data))
                    self.condition.notify()
        if failed:
            self.owner._fail("WINDOWS_PROCESS_INPUT_LIMIT")
            raise WindowsProcessIOError("WINDOWS_PROCESS_INPUT_LIMIT")

    async def drain(self):
        with self.owner._lock:
            target = self.submitted
        while True:
            with self.owner._lock:
                self.owner._raise_error()
                if self.completed >= target:
                    return
                if self.finished:
                    raise WindowsProcessIOError("WINDOWS_PROCESS_STDIN_CLOSED")
                self.changed.clear()
            await self.changed.wait()

    def close(self):
        with self.owner._lock:
            self.closed = True
            self.condition.notify()

    def is_closing(self):
        with self.owner._lock:
            return self.closed

    async def wait_closed(self):
        while True:
            with self.owner._lock:
                self.owner._raise_error()
                if self.finished:
                    return
                self.changed.clear()
            await self.changed.wait()

    def _abort(self):
        with self.owner._lock:
            self.aborted = self.closed = True
            self.queue.clear()
            self.condition.notify()

    def _pump(self):
        try:
            while True:
                with self.owner._lock:
                    while not self.queue and not self.closed:
                        self.condition.wait()
                    if self.aborted or (not self.queue and self.closed):
                        break
                    sequence, data = self.queue.popleft()
                view = memoryview(data)
                while view:
                    written = self.stream.write(view)
                    if written is None or written <= 0:
                        raise BrokenPipeError()
                    view = view[written:]
                self.stream.flush()
                with self.owner._lock:
                    self.pending_bytes -= len(data)
                    self.completed = sequence
                self.owner._notify(self.changed)
        except (OSError, ValueError, TypeError):
            if not self.owner._closing:
                self.owner._fail("WINDOWS_PROCESS_STDIN_CLOSED")
        finally:
            try:
                self.stream.close()
            except OSError:
                pass
            with self.owner._lock:
                self.finished = True
            self.owner._notify(self.changed)


class AsyncWindowsProcess:
    """Async pipe facade; ``aclose`` is mandatory after use, including success."""

    def __init__(self, process, *, max_stdout=2 * 1024 * 1024,
                 max_stderr=65536, max_line_bytes=None, max_stdin=3 * 1024 * 1024):
        max_stdout = _limit(max_stdout, "stdout")
        max_stderr = _limit(max_stderr, "stderr")
        max_stdin = _limit(max_stdin, "stdin")
        max_line_bytes = _limit(max_stdout if max_line_bytes is None else max_line_bytes, "line")
        self._process, self.pid = process, process.pid
        self._loop = asyncio.get_running_loop()
        self._lock = threading.RLock()
        self._closing = self._terminated = False
        self._error = self._returncode = None
        self._threads = []
        self._notifications = set()
        self._cleanup = None
        self._wait_changed = asyncio.Event()
        self.stdin = _Writer(self, process.stdin, max_stdin) if process.stdin is not None else None
        self.stdout = _Reader(self, process.stdout, max_stdout, max_line_bytes) if process.stdout is not None else None
        self.stderr = _Reader(self, process.stderr, max_stderr, max_stderr) if process.stderr is not None else None
        self._thread(self._waiter, "waiter")
        # Start only after all streams exist, so an early overflow can notify all.
        try:
            for thread in self._threads:
                thread.start()
        except Exception:
            self._closing = True
            self._terminate_once()
            if self.stdin is not None:
                self.stdin._abort()
            for thread in self._threads:
                if thread.ident is not None:
                    thread.join(timeout=5)
            process.close()
            raise WindowsProcessIOError("WINDOWS_PROCESS_IO_FAILED") from None

    def _thread(self, function, label):
        thread = threading.Thread(target=function, name=f"ontology-job-{label}-{self.pid}", daemon=True)
        self._threads.append(thread)
        return thread

    def _notify(self, event):
        with self._lock:
            if event in self._notifications:
                return
            self._notifications.add(event)

        def deliver():
            with self._lock:
                self._notifications.discard(event)
            event.set()

        try:
            # Coalesce wakeups: one-byte writes cannot fill the event loop's
            # callback queue independently of the bounded pipe buffers.
            self._loop.call_soon_threadsafe(deliver)
        except RuntimeError:
            with self._lock:
                self._notifications.discard(event)
            # The loop has gone away; the Job still has a synchronous owner.
            self._terminate_once()

    def _raise_error(self):
        if self._error:
            raise WindowsProcessIOError(self._error)

    def _fail(self, code):
        with self._lock:
            if self._error is None:
                self._error = code
        self._terminate_once()
        for stream in (self.stdin, self.stdout, self.stderr):
            if stream is not None:
                self._notify(stream.changed)
        self._notify(self._wait_changed)

    def _terminate_once(self):
        with self._lock:
            if self._terminated:
                return
            self._terminated = True
        try:
            self._process.terminate_tree()
        except Exception:
            with self._lock:
                self._terminated = False
                self._error = "WINDOWS_PROCESS_CLEANUP_FAILED"

    def _waiter(self):
        try:
            result = self._process.wait()
            with self._lock:
                self._returncode = result
        except Exception:
            self._fail("WINDOWS_PROCESS_IO_FAILED")
        finally:
            self._notify(self._wait_changed)

    @property
    def returncode(self):
        with self._lock:
            return self._returncode

    async def wait(self):
        while True:
            with self._lock:
                self._raise_error()
                if self._returncode is not None:
                    return self._returncode
                self._wait_changed.clear()
            await self._wait_changed.wait()

    def terminate(self):
        self._terminate_once()

    kill = terminate

    def _shutdown(self):
        with self._lock:
            self._closing = True
        self._terminate_once()
        if self.stdin is not None:
            self.stdin._abort()
        # Kill closes every child-side pipe endpoint before joining pipe owners.
        deadline = time.monotonic() + _JOIN_SECONDS
        for thread in self._threads:
            thread.join(timeout=max(0, deadline - time.monotonic()))
        if any(thread.is_alive() for thread in self._threads):
            # Even a failed TerminateJobObject must release the non-inherited
            # kill-on-close Job handle. The core also closes pipe handles and
            # makes subsequent wait() fail rather than waiting on a closed owner.
            try:
                self._process.close()
            except Exception:
                pass
            deadline = time.monotonic() + _JOIN_SECONDS
            for thread in self._threads:
                thread.join(timeout=max(0, deadline - time.monotonic()))
            raise WindowsProcessIOError("WINDOWS_PROCESS_CLEANUP_FAILED")
        try:
            self._process.close()
        except Exception:
            raise WindowsProcessIOError("WINDOWS_PROCESS_CLEANUP_FAILED") from None

    async def aclose(self):
        if self._cleanup is None:
            self._cleanup = asyncio.create_task(asyncio.to_thread(self._shutdown))
        try:
            await asyncio.shield(self._cleanup)
        except asyncio.CancelledError:
            await _finish(self._cleanup)
            raise

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.aclose()


def _spawn(*args, **kwargs):
    # Delay platform import so bounded-pipe tests can run with mock owners on
    # Linux and importing an unrelated backend module never loads Win32 DLLs.
    from .windows_process import spawn_job_process
    return spawn_job_process(*args, **kwargs)


async def async_spawn(argv, *, cwd, env, limits=None, appcontainer=None,
                      stdin=PIPE, stdout=PIPE, stderr=PIPE,
                      max_stdout=2 * 1024 * 1024, max_stderr=65536,
                      max_line_bytes=None, max_stdin=3 * 1024 * 1024):
    """Spawn with native Job ownership and bounded, cancellation-safe pipes."""
    options = dict(max_stdout=_limit(max_stdout, "stdout"),
                   max_stderr=_limit(max_stderr, "stderr"),
                   max_stdin=_limit(max_stdin, "stdin"),
                   max_line_bytes=_limit(max_stdout if max_line_bytes is None else max_line_bytes, "line"))
    task = asyncio.create_task(asyncio.to_thread(
        _spawn, argv, cwd=cwd, env=env, limits=limits, appcontainer=appcontainer,
        stdin=stdin, stdout=stdout, stderr=stderr))
    try:
        process = await asyncio.shield(task)
    except asyncio.CancelledError:
        process = await _finish(task)
        adapter = AsyncWindowsProcess(process, **options)
        await _finish(asyncio.create_task(adapter.aclose()))
        raise
    return AsyncWindowsProcess(process, **options)
