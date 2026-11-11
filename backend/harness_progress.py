"""Authenticated memory-only progress channel owned by one Harness invocation."""
from __future__ import annotations
import asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import secrets
import threading

MAX_BYTES = 2 * 1024 * 1024
MESSAGES = {'正在理解业务要求。', '正在查阅参考内容。', '正在构建技能提案。',
            '正在执行获准的查询或技能。', '正在整理回答。'}


class ProgressChannel:
    def __init__(self, callback):
        self.loop = asyncio.get_running_loop()
        self.callback, self.queue = callback, asyncio.Queue(maxsize=256)
        self.token, self.received, self.failed = secrets.token_urlsafe(32), 0, False
        owner = self
        class Server(HTTPServer):
            def get_request(self):
                sock, address = super().get_request()
                sock.settimeout(2)
                return sock, address
            def handle_error(self, *_):
                pass
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def reply(self, status):
                self.send_response(status)
                self.send_header('Content-Length', '0')
                self.end_headers()
            def do_POST(self):
                values = self.headers.get_all('Authorization', [])
                if (self.path != '/events' or len(values) != 1 or self.headers.get('Proxy-Authorization')
                        or not secrets.compare_digest(values[0].encode('utf-8'), ('Bearer ' + owner.token).encode('ascii'))):
                    self.reply(403); return
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 1 <= length <= 16384:
                        raise ValueError()
                    raw = self.rfile.read(length)
                    value = json.loads(raw)
                    if not isinstance(value, dict):
                        raise ValueError()
                    kind = value.get('type')
                    if kind == 'progress':
                        if (set(value) - {'type', 'stage', 'message', 'tool'} or value.get('message') not in MESSAGES
                                or value.get('stage') not in {'model', 'tool', 'generating'}):
                            raise ValueError()
                    elif kind == 'answer_delta':
                        if (set(value) != {'type', 'text', 'message_id'} or not isinstance(value['text'], str)
                                or len(value['text']) > 8192 or not isinstance(value['message_id'], str)
                                or not 1 <= len(value['message_id']) <= 160):
                            raise ValueError()
                    else:
                        raise ValueError()
                    owner.received += len(raw)
                    if owner.received > MAX_BYTES:
                        raise ValueError()
                    owner.loop.call_soon_threadsafe(owner.enqueue, value)
                except Exception:
                    owner.failed = True
                    self.reply(422); return
                self.reply(200)
            def do_GET(self):
                self.reply(405)
        self.server = Server(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
        self.thread.start()

    @property
    def contract(self):
        return {'url': 'http://127.0.0.1:' + str(self.server.server_port) + '/events', 'token': self.token}

    def enqueue(self, value):
        try:
            self.queue.put_nowait(value)
        except asyncio.QueueFull:
            self.failed = True

    async def pump(self, exchange):
        from .skill_harness import _error
        while True:
            if self.failed:
                raise _error('HARNESS_LIMIT')
            try:
                value = await asyncio.wait_for(self.queue.get(), .1)
                self.callback(value)
            except TimeoutError:
                pass
            if exchange.done() and self.queue.empty():
                await asyncio.sleep(0)
                if self.queue.empty():
                    return

    async def close(self):
        await asyncio.to_thread(self.server.shutdown)
        self.server.server_close()
        await asyncio.to_thread(self.thread.join, 3)
        if self.thread.is_alive():
            raise RuntimeError('Owned progress channel did not close')
