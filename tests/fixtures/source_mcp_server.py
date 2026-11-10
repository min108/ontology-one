"""Local, real MCP Streamable HTTP fixture for ontology-source/1.

State and bearer credentials are supplied in a private test-owned JSON file.
Only fixed resources are queried; no SQL, interpreter or arbitrary tool gateway.
The event file records call counters only, never source values or credentials.
"""
from __future__ import annotations
import argparse
import asyncio
import hmac
import json
import os
from pathlib import Path
import time
from typing import Any

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel
import uvicorn


class Discovery(BaseModel):
    contract_version: str
    resources: list[dict[str, Any]]


class QueryResult(BaseModel):
    contract_version: str
    columns: list[str]
    rows: list[dict[str, Any]]
    truncated: bool
    elapsed_ms: float


class IncompatibleDiscovery(BaseModel):
    contract_version: str
    unsupported: str


def create_app(state_path: Path):
    def read():
        return json.loads(state_path.read_text())

    events_path = state_path.with_suffix('.events.json')
    counters = {'discover': 0, 'query_started': 0, 'query_finished': 0}

    def mark(name):
        counters[name] += 1
        temporary = events_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(counters))
        temporary.chmod(0o600)
        temporary.replace(events_path)

    server = MCPServer('Ontology source integration fixture', version='1.0', log_level='ERROR')

    if read().get('schema_mode') == 'incompatible':
        @server.tool(name='ontology_source_discover', structured_output=True)
        async def incompatible_discover(contract_version: str) -> IncompatibleDiscovery:
            return IncompatibleDiscovery(contract_version='1.0', unsupported='fixture')
    else:
        @server.tool(name='ontology_source_discover', structured_output=True)
        async def discover(contract_version: str) -> Discovery:
            if contract_version != '1.0':
                raise ValueError('Unsupported contract version')
            mark('discover')
            state = read()
            if state.get('mode') == 'tool_error':
                raise ValueError('Fixture upstream failure')
            resources = state['resources']
            if state.get('mode') == 'oversized_metadata':
                resources = [{**resources[0], 'columns': resources[0]['columns'] * 51}]
            return Discovery(contract_version='9.0' if state.get('mode') == 'wrong_contract' else '1.0', resources=resources)

    @server.tool(name='ontology_source_query', structured_output=True)
    async def query(contract_version: str, resource: str, selection: dict[str, Any]) -> QueryResult:
        began = time.monotonic()
        if contract_version != '1.0':
            raise ValueError('Unsupported contract version')
        state = read()
        metadata = next((item for item in state['resources'] if item['key'] == resource), None)
        if metadata is None:
            raise ValueError('Unknown fixed resource')
        mark('query_started')
        while read().get('query_hold') and time.monotonic() - began < 12:
            await asyncio.sleep(.025)
        state = read()
        if state.get('query_delay'):
            await asyncio.sleep(min(float(state['query_delay']), 8))
        rows = [dict(row) for row in state['rows']]
        # These definitions are fixed fixture logic, never supplied as SQL.
        if metadata['kind'] in ('view', 'saved_query'):
            rows = [row for row in rows if row['status'] == 'active']
        allowed = {column['name'] for column in metadata['columns']}
        columns = selection['columns']
        if not columns or not set(columns).issubset(allowed):
            raise ValueError('Unknown selected column')
        for item in selection.get('filters', []):
            column, op, value = item['column'], item['op'], item.get('value')
            if column not in allowed:
                raise ValueError('Unknown filter column')
            def matches(row):
                actual = row.get(column)
                if op == 'is_null': return actual is None
                if op == 'eq': return actual == value
                if op == 'ne': return actual != value
                if op == 'in': return actual in value
                if actual is None: return False
                return {'gt': lambda: actual > value, 'gte': lambda: actual >= value,
                        'lt': lambda: actual < value, 'lte': lambda: actual <= value}[op]()
            rows = [row for row in rows if matches(row)]
        for order in reversed(selection.get('order_by', [])):
            if order['column'] not in allowed or order['direction'] not in ('asc', 'desc'):
                raise ValueError('Unknown ordering')
            rows.sort(key=lambda row: (row.get(order['column']) is None, row.get(order['column'])), reverse=order['direction'] == 'desc')
        limit = selection['limit']
        result = [{column: row.get(column) for column in columns} for row in rows[:limit]]
        if state.get('mode') == 'oversized_cell' and result:
            result[0][columns[0]] = 'x' * 20000
        mark('query_finished')
        return QueryResult(contract_version='1.0', columns=columns, rows=result,
                           truncated=len(rows) > limit, elapsed_ms=(time.monotonic() - began) * 1000)

    application = server.streamable_http_app(json_response=True, stateless_http=False, host='127.0.0.1')

    async def authenticated(scope, receive, send):
        if scope['type'] == 'http':
            headers = dict(scope.get('headers', []))
            expected = ('Bearer ' + read()['token']).encode()
            if not hmac.compare_digest(headers.get(b'authorization', b''), expected):
                body = b'{"detail":"Authentication required"}'
                await send({'type': 'http.response.start', 'status': 401, 'headers': [(b'content-type', b'application/json')]})
                await send({'type': 'http.response.body', 'body': body})
                return
        await application(scope, receive, send)

    return authenticated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8890)
    parser.add_argument('--state-file', type=Path, required=True)
    args = parser.parse_args()
    if args.state_file.stat().st_mode & 0o077:
        raise SystemExit('Fixture state must have mode 600')
    uvicorn.run(create_app(args.state_file), host='127.0.0.1', port=args.port, access_log=False, log_level='error')


if __name__ == '__main__':
    main()
