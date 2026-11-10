"""Minimal upstream MCP caller. Prints metadata only; never persists query rows.

ONTOLOGY_MCP_URL selects the server endpoint (default local /mcp/).
ONTOLOGY_MCP_TOKEN can supply the application credential; otherwise getpass asks
without echo. Query/explain/join arguments are read as one JSON object from stdin,
never as shell arguments. This is a client example, not a token provisioning tool.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def call(endpoint, credential, tool_name, arguments):
    async with httpx2.AsyncClient(
        headers={'Authorization': 'Bearer ' + credential},
        timeout=httpx2.Timeout(40), follow_redirects=False,
    ) as client:
        async with streamable_http_client(endpoint, http_client=client) as streams:
            async with ClientSession(*streams, read_timeout_seconds=40) as session:
                initialized = await session.initialize()
                listing = await session.list_tools()
                names = [tool.name for tool in listing.tools]
                if tool_name not in names:
                    raise RuntimeError('当前应用未获该工具的访问授权。')
                result = await session.call_tool(tool_name, arguments, read_timeout_seconds=40)
                envelope = result.structured_content or {}
                data = envelope.get('data') or {}
                # A real upstream application may consume data in memory here.
                # Keep the distributed example safe for terminals/CI logs.
                summary = {
                    'server': initialized.server_info.name,
                    'contract_version': envelope.get('contract_version'),
                    'request_id': envelope.get('request_id'),
                    'tool': tool_name,
                    'status': envelope.get('status'),
                    'error_code': (envelope.get('error') or {}).get('code'),
                    'data_fields': sorted(data),
                    'counts': {key: len(value) for key, value in data.items() if isinstance(value, list)},
                    'warning_codes': [item.get('code') for item in envelope.get('warnings', [])],
                    'source_receipts': len(envelope.get('provenance', [])),
                    'result_values_printed': False,
                }
                print(json.dumps(summary, ensure_ascii=False, indent=2))
                return 1 if result.is_error or envelope.get('status') == 'error' else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tool', default='ontology_discover', choices=[
        'ontology_discover', 'ontology_query', 'ontology_traverse', 'ontology_explain', 'ontology_join',
    ])
    parser.add_argument('--stdin', action='store_true', help='Read a bounded tool-argument JSON object from stdin.')
    args = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(128 * 1024 + 1) if args.stdin else b'{}'
        if len(raw) > 128 * 1024:
            raise ValueError()
        arguments = json.loads(raw)
        if not isinstance(arguments, dict):
            raise ValueError()
    except (ValueError, UnicodeError):
        parser.error('输入须为128KiB以内的JSON对象。')
    credential = os.getenv('ONTOLOGY_MCP_TOKEN') or getpass.getpass('应用令牌（不回显）: ')
    endpoint = os.getenv('ONTOLOGY_MCP_URL', 'http://127.0.0.1:8765/mcp/')
    if not credential.startswith('onto_app_'):
        parser.error('请使用集成应用中签发的应用令牌。')
    try:
        return asyncio.run(call(endpoint, credential, args.tool, arguments))
    except Exception:
        # Library exceptions may include HTTP headers or argument/result content.
        print('MCP调用未完成，请核对端点、应用凭据、授权和服务状态。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
