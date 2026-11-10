"""One-shot, resource-bounded MCP domain worker. Its pipes are private IPC.

The parent supplies a server-authenticated identity, never a bearer credential.
Business rows exist only in this process and its bounded response pipe.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import sys

if os.name != 'nt':
    import resource

MAX_INPUT_BYTES = 160 * 1024
MAX_RESULT_BYTES = 2 * 1024 * 1024


def _limits():
    if os.name == 'nt':
        from .windows_process import assert_job_limits
        memory = max(256, min(1536, int(os.getenv('ONTOLOGY_MCP_WORKER_MEMORY_MB', '768')))) * 1024 * 1024
        assert_job_limits(max_memory_bytes=memory, max_processes=4, max_cpu_time_seconds=15)
        return
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    memory = max(256, min(1536, int(os.getenv('ONTOLOGY_MCP_WORKER_MEMORY_MB', '768')))) * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))


def _decode(raw):
    def invalid(_):
        raise ValueError('Invalid JSON scalar')
    return json.loads(raw, parse_constant=invalid)


def _read_frame(stream, limit):
    raw = stream.readline(limit + 2)
    if not raw or len(raw) > limit + 1 or not raw.endswith(b'\n'):
        raise ValueError('Invalid worker frame')
    return _decode(raw)


def _write_frame(fd, value, limit=MAX_RESULT_BYTES):
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
    if len(raw) > limit:
        raise ValueError('Worker result exceeds limit')
    view = memoryview(raw + b'\n')
    while view:
        sent = os.write(fd, view)
        view = view[sent:]


def _audit(principal, tool, request_id, outcome):
    from .security.store import append_audit
    append_audit(principal, 'mcp.' + outcome, tool, outcome,
                 {'tool': tool, 'request_id': request_id})


def main():
    # Save only the private protocol descriptor. Suppress accidental prints,
    # third-party logs and tracebacks before importing business modules.
    output = os.dup(sys.stdout.fileno())
    if os.name == 'nt':
        import msvcrt
        msvcrt.setmode(output, os.O_BINARY)
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
    sink = os.open(os.devnull, os.O_WRONLY)
    os.dup2(sink, sys.stdout.fileno())
    os.dup2(sink, sys.stderr.fileno())
    os.close(sink)
    logging.disable(logging.CRITICAL)
    request_id = 'worker'
    try:
        _limits()
        payload = _read_frame(sys.stdin.buffer, MAX_INPUT_BYTES)
        if not isinstance(payload, dict) or set(payload) != {'principal', 'tool', 'arguments', 'request_id', 'mode'}:
            raise ValueError('Invalid worker request')
        request_id = payload['request_id']
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
            raise ValueError('Invalid request identifier')
        from .security.context import Principal, use_context
        from .security.applications import assert_application_current
        from .mcp_contracts import TOOL_MODELS, bounded_envelope, safe_failure
        principal = Principal(**payload['principal'])
        if principal.identity_kind != 'application' or principal.request_id != request_id:
            raise ValueError('Invalid worker identity')
        tool = payload['tool']
        if tool not in TOOL_MODELS:
            raise ValueError('Invalid worker tool')
        with use_context(principal):
            if payload['mode'] == 'audit':
                # Revoked identities must still leave cancellation/timeout audit
                # metadata. This private mode grants no domain or source access.
                outcome = payload['arguments'].get('outcome')
                if set(payload['arguments']) != {'outcome'} or outcome not in {'timeout', 'cancelled', 'result_limit', 'failed'}:
                    raise ValueError('Invalid audit event')
                _audit(principal, tool, request_id, outcome)
                _write_frame(output, {'audited': True}, 1024)
                return
            if payload['mode'] != 'execute':
                raise ValueError('Invalid worker mode')
            from .mcp_tools import dispatch_tool, validate_receipts
            try:
                assert_application_current(principal)
                _audit(principal, tool, request_id, 'started')
                envelope = bounded_envelope(dispatch_tool(tool, payload['arguments'], request_id))
            except Exception as exc:
                envelope = safe_failure(exc, request_id)
            # The parent deliberately sees no provisional rows. It requests a
            # final, fresh authorization/receipt check in this killable process.
            _write_frame(output, {'ready': True, 'request_id': request_id}, 1024)
            if _read_frame(sys.stdin.buffer, 1024) != {'finalize': True}:
                raise ValueError('Invalid finalize frame')
            try:
                assert_application_current(principal)
                if envelope.status != 'error':
                    validate_receipts(envelope.provenance)
                    assert_application_current(principal)
                envelope = bounded_envelope(envelope)
                _audit(principal, tool, request_id, 'finished' if envelope.status != 'error' else 'failed')
            except Exception as exc:
                envelope = safe_failure(exc, request_id)
            _write_frame(output, envelope.model_dump(mode='json'))
    except BaseException:
        # Even import failures/resource exhaustion expose no traceback or input.
        with contextlib.suppress(BaseException):
            _write_frame(output, {
                'contract_version': '1.0', 'request_id': request_id,
                'status': 'error', 'data': None,
                'error': {'code': 'INTERNAL_ERROR', 'message': '工具执行暂不可用。', 'retryable': False},
                'warnings': [], 'provenance': [],
                'retention': {'result': 'response_only', 'cache': 'disabled', 'audit': 'metadata_only'},
            })
    finally:
        os.close(output)


if __name__ == '__main__':
    main()
