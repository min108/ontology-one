"""Deterministic Messages fixture for actual Harness agent/task integration.

Only model content is simulated. Identity, authorization, MCP, published skill
loading, SDK orchestration and process cleanup run in the application. The
private state file contains synthetic credentials/plans; the events file keeps
only fixed counters, never request bodies, model text or external business rows.
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
    counters = {'requests': 0, 'authenticated': 0, 'unexpected_tools': 0,
                'foreign_value_seen': 0, 'tool_results_seen': 0,
                'selected_rows_seen': 0,
                'held': 0, 'completed': 0, 'disconnected': 0}

    def state():
        return json.loads(state_path.read_text(encoding='utf-8'))

    def mark(**values):
        with lock:
            for key, value in values.items():
                counters[key] += value
            temporary = events_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(counters), encoding='utf-8')
            temporary.chmod(0o600)
            temporary.replace(events_path)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_):
            pass

        def respond(self, status, value, content_type='application/json'):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(value)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:
                self.wfile.write(value)
            except (BrokenPipeError, ConnectionResetError):
                mark(disconnected=1)

        def do_GET(self):
            self.respond(200 if self.path == '/health' else 404, b'{}')

        def do_POST(self):
            configuration = state()
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 4 * 1024 * 1024:
                self.respond(413, b'{}'); return
            raw = self.rfile.read(length)
            mark(requests=1)
            if not hmac.compare_digest(self.headers.get('x-api-key', ''), configuration['api_key']):
                self.respond(401, b'{"type":"error","error":{"type":"authentication_error","message":"Synthetic fixture authentication rejected"}}')
                return
            mark(authenticated=1)
            if any(value.encode() in raw for value in configuration.get('forbidden', [])):
                mark(foreign_value_seen=1)
            if any(value.encode() in raw for value in configuration.get('expected_values', [])):
                mark(selected_rows_seen=1)
            try:
                body = json.loads(raw)
                names = {item['name'] for item in body.get('tools', [])}
                if names - set(configuration.get('allowed_tools', names)):
                    mark(unexpected_tools=1)
                results = [part for message in body.get('messages', [])
                           if isinstance(message.get('content'), list)
                           for part in message['content'] if isinstance(part, dict) and part.get('type') == 'tool_result']
                index = len(results)
                if results:
                    mark(tool_results_seen=1)
                plan = configuration.get('plan', [])
                content = plan[min(index, len(plan) - 1)] if plan else {'type': 'text', 'text': '合成模型已完成只读分析。'}
            except (KeyError, TypeError, ValueError, IndexError):
                self.respond(400, b'{}'); return
            if configuration.get('mode') == 'unavailable':
                self.respond(503, b'{"type":"error","error":{"type":"overloaded_error","message":"Synthetic fixture unavailable"}}')
                return
            began = time.monotonic()
            if configuration.get('mode') == 'hold' and index >= configuration.get('hold_at', 0):
                mark(held=1)
                while state().get('mode') == 'hold' and time.monotonic() - began < 100:
                    time.sleep(.05)
            if content['type'] != 'tool_use':
                mark(completed=1)
            events = [
                {'type': 'message_start', 'message': {'id': 'synthetic-agent-message', 'type': 'message',
                 'role': 'assistant', 'model': body.get('model', 'deepseek-flash'), 'content': [],
                 'usage': {'input_tokens': 8, 'output_tokens': 0}}},
                {'type': 'content_block_start', 'index': 0, 'content_block': content},
                {'type': 'content_block_stop', 'index': 0},
                {'type': 'message_delta', 'delta': {'stop_reason': 'tool_use' if content['type'] == 'tool_use' else 'end_turn'},
                 'usage': {'output_tokens': 8}},
                {'type': 'message_stop'},
            ]
            wire = ''.join('event: ' + item['type'] + '\ndata: ' + json.dumps(item, ensure_ascii=False) + '\n\n'
                           for item in events).encode('utf-8')
            self.respond(200, wire, 'text/event-stream')

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-file', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    arguments = parser.parse_args()
    serve(arguments.state_file, arguments.port)
