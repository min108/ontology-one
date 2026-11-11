"""Private-pipe worker for actual session tools. No tokens or model credentials."""
from __future__ import annotations

import contextlib
import logging
import os
import re
import sys

from .mcp_worker import _limits, _read_frame, _write_frame, MAX_INPUT_BYTES


def _hydrate_ledger(bridge, arguments, principal, mapping_ids, skill_ids):
    """Private metadata IPC only; every hydrated proof is rechecked in validate."""
    from .mcp_contracts import Receipt
    if not isinstance(arguments, dict) or set(arguments) != {'receipts', 'skill_versions', 'group_versions', 'asset_versions'}:
        raise ValueError()
    receipts = arguments['receipts']
    skills, groups, assets = (arguments[key] for key in ('skill_versions', 'group_versions', 'asset_versions'))
    if (not isinstance(receipts, list) or len(receipts) > 64 or not isinstance(skills, dict)
            or len(skills) > 16 or set(skills) - set(skill_ids)):
        raise ValueError()
    checked = [Receipt.model_validate(receipt, strict=True).model_dump(mode='python') for receipt in receipts]
    if any(receipt['tenant_id'] != principal.tenant_id or receipt['mapping_id'] not in mapping_ids for receipt in checked):
        from fastapi import HTTPException
        raise HTTPException(403, '来源证明不属于当前授权范围。')
    versions = {}
    for ident, version in skills.items():
        if (not isinstance(version, (tuple, list)) or len(version) != 2 or type(version[0]) is not int or version[0] < 1
                or not isinstance(version[1], str) or not re.fullmatch(r'[a-f0-9]{64}', version[1])):
            raise ValueError()
        versions[ident] = tuple(version)
    for values, grouped in ((groups, True), (assets, False)):
        if not isinstance(values, dict) or len(values) > 100:
            raise ValueError()
        for key, version in values.items():
            if (not isinstance(key, str) or not 1 <= len(key) <= (400 if grouped else 191)
                    or type(version) is not int or version < 1
                    or (grouped and ('/' not in key or not key.split('/', 1)[0] or (not key.split('/', 1)[1] and version not in (1, 2))))):
                raise ValueError()
    bridge.receipts, bridge.skill_versions = checked, versions
    bridge.group_versions, bridge.asset_versions = dict(groups), dict(assets)


def main():
    output = os.dup(sys.stdout.fileno())
    if os.name == 'nt':
        import msvcrt
        msvcrt.setmode(output, os.O_BINARY)
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
    sink = os.open(os.devnull, os.O_WRONLY)
    os.dup2(sink, sys.stdout.fileno()); os.dup2(sink, sys.stderr.fileno()); os.close(sink)
    logging.disable(logging.CRITICAL)
    requestid = 'worker'
    try:
        _limits()
        payload = _read_frame(sys.stdin.buffer, MAX_INPUT_BYTES)
        if (not isinstance(payload, dict) or set(payload) != {'principal', 'mapping_ids', 'skill_ids', 'tool', 'arguments', 'request_id'}
                or not isinstance(payload['request_id'], str) or not 1 <= len(payload['request_id']) <= 100):
            raise ValueError()
        requestid = payload['request_id']
        # Published script execution stays in the parent-owned cooperative
        # sandbox path, whose cancellation cleanup owns its profiles as well.
        # This killable source worker cannot accept a script-execution request.
        if payload['tool'] == 'skill_execute':
            raise ValueError()
        for name, maximum in (('mapping_ids', 64), ('skill_ids', 16)):
            values = payload[name]
            if (not isinstance(values, list) or len(values) > maximum or len(set(values)) != len(values)
                    or any(not isinstance(value, str) or not 1 <= len(value) <= 191 for value in values)):
                raise ValueError()
        from .security.context import Principal, use_context
        from .security.store import assert_principal_current
        from .agent_tools import SessionToolBridge
        principal = Principal(**payload['principal'])
        if principal.identity_kind != 'session' or principal.request_id != requestid:
            raise ValueError()
        with use_context(principal):
            assert_principal_current(principal)
            bridge = SessionToolBridge(principal, payload['mapping_ids'], payload['skill_ids'])
            if payload['tool'] == '__validate_ledger':
                _hydrate_ledger(bridge, payload['arguments'], principal, payload['mapping_ids'], payload['skill_ids'])
                value = {'valid': True}
            else:
                value = bridge.invoke_local(payload['tool'], payload['arguments'])
            # No provisional data leaves the worker. Finalization performs a
            # fresh source/skill/identity check inside the killable process.
            _write_frame(output, {'ready': True, 'request_id': requestid}, 1024)
            if _read_frame(sys.stdin.buffer, 1024) != {'finalize': True}:
                raise ValueError()
            bridge.validate()
            assert_principal_current(principal)
            _write_frame(output, {'ok': True, 'request_id': requestid, 'value': value,
                                  'receipts': bridge.receipts, 'skill_versions': bridge.skill_versions,
                                  'group_versions': getattr(bridge, 'group_versions', {}),
                                  'asset_versions': getattr(bridge, 'asset_versions', {})})
    except BaseException as failure:
        with contextlib.suppress(BaseException):
            from .mcp_contracts import safe_failure
            error = safe_failure(failure, requestid).error.model_dump(mode='python')
            _write_frame(output, {'ok': False, 'request_id': requestid, 'error': error}, 2048)
    finally:
        os.close(output)


if __name__ == '__main__': main()
