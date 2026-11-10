"""Deterministic Messages/SSE model fixture for the *real* official Harness SDK.

Only model responses are simulated. The fixture never implements platform
identity, skill governance, Harness orchestration, or script execution. Private
configuration lives in the caller's disposable directory; events are counters.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
from pathlib import Path
import threading
import time


def serve(state_path: Path, port: int):
    events_path = state_path.with_suffix('.events.json')
    lock = threading.Lock()
    counters = {'requests': 0, 'authenticated': 0, 'foreign_value_seen': 0,
                'unexpected_tools': 0, 'read_requests': 0, 'proposal_requests': 0,
                'completed': 0, 'disconnected': 0}

    def state():
        return json.loads(state_path.read_text())

    def mark(**values):
        with lock:
            for key, value in values.items():
                counters[key] += value
            temporary = events_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(counters))
            temporary.chmod(0o600)
            temporary.replace(events_path)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_):
            pass

        def respond(self, code, body, content_type='application/json'):
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                mark(disconnected=1)

        def do_GET(self):
            self.respond(200 if self.path == '/health' else 404, b'{}')

        def do_POST(self):
            configuration = state()
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 4 * 1024 * 1024:
                self.respond(413, b'{}')
                return
            raw = self.rfile.read(length)
            mark(requests=1)
            if not hmac.compare_digest(self.headers.get('x-api-key', ''), configuration['api_key']):
                self.respond(401, b'{"type":"error","error":{"type":"authentication_error","message":"Fixture authentication rejected"}}')
                return
            mark(authenticated=1)
            if any(value.encode() in raw for value in configuration.get('forbidden', [])):
                mark(foreign_value_seen=1)
            try:
                body = json.loads(raw)
                tool_names = {item['name'] for item in body.get('tools', [])}
                if tool_names - {'read_skill_file', 'propose_skill_files'}:
                    mark(unexpected_tools=1)
                names = {part.get('name') for message in body.get('messages', [])
                         if isinstance(message.get('content'), list)
                         for part in message['content'] if isinstance(part, dict) and part.get('type') == 'tool_use'}
            except (KeyError, TypeError, ValueError):
                self.respond(400, b'{}')
                return
            if configuration.get('mode') == 'unavailable':
                self.respond(503, b'{"type":"error","error":{"type":"overloaded_error","message":"Fixture unavailable"}}')
                return
            began = time.monotonic()
            while state().get('mode') == 'hold' and time.monotonic() - began < 100:
                time.sleep(.05)
            if configuration.get('mode') == 'forbidden_tool' and 'exec_command' not in names:
                content = {'type': 'tool_use', 'id': 'fixture-forbidden', 'name': 'exec_command',
                           'input': {'cmd': 'printf forbidden'}}
                stop_reason = 'tool_use'
            elif 'read_skill_file' not in names:
                mark(read_requests=1)
                content = {'type': 'tool_use', 'id': 'fixture-read', 'name': 'read_skill_file',
                           'input': {'path': 'SKILL.md'}}
                stop_reason = 'tool_use'
            elif 'propose_skill_files' not in names:
                mark(proposal_requests=1)
                content = {'type': 'tool_use', 'id': 'fixture-propose', 'name': 'propose_skill_files',
                           'input': {'summary': '已根据技能目标补充待审参考说明。',
                                     'changes': [{'path': 'references/ai-review-note.md',
                                                  'content': '# 待核对说明\n\n' + configuration['proposal_marker'] + '\n'}],
                                     'delete_paths': [], 'manifest_patch': {}}}
                stop_reason = 'tool_use'
            else:
                mark(completed=1)
                content = {'type': 'text', 'text': '已生成候选修改，请由用户核对后保存。'}
                stop_reason = 'end_turn'
            events = [
                {'type': 'message_start', 'message': {'id': 'fixture-message', 'type': 'message',
                 'role': 'assistant', 'model': body.get('model', 'deepseek-v4-flash'), 'content': [],
                 'usage': {'input_tokens': 8, 'output_tokens': 0}}},
                {'type': 'content_block_start', 'index': 0, 'content_block': content},
                {'type': 'content_block_stop', 'index': 0},
                {'type': 'message_delta', 'delta': {'stop_reason': stop_reason}, 'usage': {'output_tokens': 8}},
                {'type': 'message_stop'},
            ]
            wire = ''.join('event: ' + item['type'] + '\ndata: ' + json.dumps(item, ensure_ascii=False) + '\n\n'
                           for item in events).encode()
            self.respond(200, wire, 'text/event-stream')

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-file', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8909)
    options = parser.parse_args()
    serve(options.state_file, options.port)
