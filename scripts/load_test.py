"""Closed-loop load on a private uvicorn instance using real password sessions.

Default: 20 institution databases, 50 simultaneously active HTTP sessions,
1,000 entities per institution, 10 seconds warmup and 90 seconds measurement.
Every institution uses the same IDs to expose accidental cache/data leakage.
SQL is used only to prepare synthetic volume; auth and measured operations use HTTP.
"""
from __future__ import annotations
import argparse
import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import time

import httpx
import pymysql
from check_security import Sandbox, Session, expect


def percentile(values, ratio):
    return round(sorted(values)[max(0, math.ceil(len(values) * ratio) - 1)], 3) if values else None


def hardware(pid, workers):
    info = {'platform': platform.platform(), 'python': platform.python_version(), 'host_logical_cpus': os.cpu_count(),
            'uvicorn_workers': workers, 'transport': 'HTTP/1.1 loopback, one in-flight request per authenticated session'}
    for name in ('cpu.max', 'memory.max', 'cpuset.cpus.effective'):
        path = Path('/sys/fs/cgroup') / name
        if path.is_file():
            info['cgroup_' + name] = path.read_text().strip()
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemTotal:'):
            info['host_mem_total'] = line.split(':', 1)[1].strip()
    if Path('/proc/cpuinfo').exists():
        info['cpu_model'] = next((line.split(':', 1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines() if line.startswith('model name')), '')
    info['api_pid'] = pid
    return info


def process_stats(pid, seen=None, tree=None):
    seen = set() if seen is None else seen
    if tree is None:
        # The managed /proc surface omits task/<pid>/children; derive PPIDs from stat.
        tree = defaultdict(list)
        for path in Path('/proc').iterdir():
            if not path.name.isdigit():
                continue
            try:
                fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
                tree[int(fields[1])].append(int(path.name))
            except (OSError, ValueError, IndexError):
                continue
    result = {'rss_kib': 0, 'cpu_seconds': 0, 'errors': [], 'observed_processes': 0}
    if pid in seen:
        return result
    seen.add(pid)
    try:
        status = dict(line.split(':', 1) for line in Path(f'/proc/{pid}/status').read_text().splitlines() if ':' in line)
        result['rss_kib'] = int(status.get('VmRSS', '0').split()[0])
        result['observed_processes'] = 1
    except (OSError, ValueError, KeyError) as exc:
        result['errors'].append('proc_status:' + type(exc).__name__)
    try:
        # comm may contain spaces: fields after the final ')' start at field 3.
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        result['cpu_seconds'] = (int(fields[11]) + int(fields[12])) / os.sysconf('SC_CLK_TCK')
    except (OSError, ValueError, KeyError, IndexError) as exc:
        result['errors'].append('proc_stat:' + type(exc).__name__)
    for child in tree.get(pid, []):
        child_stats = process_stats(child, seen, tree)
        for key in ('rss_kib', 'cpu_seconds', 'observed_processes'):
            result[key] += child_stats[key]
        result['errors'].extend(child_stats['errors'])
    return result


def seed_volume(box, actor, count):
    database = box.tenant_db(actor.workspace)
    stamp = datetime.now(timezone.utc).isoformat()
    rows, ownership, relations = [], [], []
    for index in range(count):
        ident = f'LOAD-{index:06d}'
        payload = json.dumps({'definition': f'合同和项目数据定义 {index}', 'content': '用于集团业务建模、资产检索和关系查询。' * 16,
                              'category': '项目管理', 'subcategory': '计划执行'}, ensure_ascii=False)
        rows.append((ident, 'data', f'LOAD/{actor.workspace}/项目字段{index:06d}', '项目数据合约、口径与责任部门', '项目管理', actor.workspace,
                     'published' if index % 5 else 'draft', 'load-test', payload, stamp, stamp, 1))
        ownership.append((ident, actor.workspace, actor.user['id'], stamp, actor.user['id'], stamp))
        if index % 2:
            relations.append((f'LOAD-REL-{index:06d}', ident, f'LOAD-{index - 1:06d}', 'references_data', '字段关联', 'confirmed', '{}'))
    with pymysql.connect(**box.settings, database=database, charset='utf8mb4') as conn, conn.cursor() as cursor:
        cursor.executemany('INSERT INTO entities VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', rows)
        cursor.executemany('INSERT INTO entity_ownership VALUES(%s,%s,%s,%s,%s,%s)', ownership)
        cursor.executemany('INSERT INTO relations VALUES(%s,%s,%s,%s,%s,%s,%s)', relations)
        conn.commit()
    content = (f'# LOAD/{actor.workspace}/文件\n' + '项目采购合同数据说明。' * 1500).encode()
    result = actor.ok('POST', '/materials/upload', files={'file': ('load-material.md', content, 'text/markdown')},
                      data={'org_id': actor.workspace, 'upload_token': 'load-same-token-00000001'}).json()
    return {'material_id': result['id'], 'file_sha256': hashlib.sha256(content).hexdigest(), 'file_bytes': len(content)}


def cgroup_cpu():
    try:
        return {key: int(value) for key, value in (line.split() for line in Path('/sys/fs/cgroup/cpu.stat').read_text().splitlines())}
    except (OSError, ValueError):
        return {}


async def exercise(box, actors, docs, duration, warmup, expected_assets, mysql_pid=0):
    samples, errors, response_bytes, successful_writes = [], Counter(), 0, 0
    cgroup_started = cgroup_cpu()
    mysql_started = process_stats(mysql_pid) if mysql_pid else None
    mysql_peak_rss = mysql_started['rss_kib'] if mysql_started else 0
    initial_stats = process_stats(box.process.pid)
    peak_rss = initial_stats['rss_kib']
    cpu_start = initial_stats['cpu_seconds']
    resource_errors = set(initial_stats['errors'])
    observed_processes = initial_stats['observed_processes']
    ready = asyncio.Event()
    clients = [httpx.AsyncClient(base_url=box.url, cookies=actor.client.cookies,
                headers={'X-CSRF-Token': actor.csrf, 'X-Workspace-Id': actor.workspace},
                timeout=30, trust_env=False, limits=httpx.Limits(max_connections=1, max_keepalive_connections=1)) for actor in actors]
    start, stop = 0.0, 0.0

    async def worker(index, actor, client):
        nonlocal response_bytes, successful_writes
        await ready.wait()
        sequence = 0
        while time.perf_counter() < stop:
            op = sequence % 10
            kwargs = {}
            if actor.workspace == 'hq':
                category = 'hq_aggregate'
                method, path = 'GET', ('/federation/overview', '/federation/search?page_size=30&q=LOAD', '/federation/graph?limit=100')[sequence % 3]
            elif op in (0, 1, 2):
                category, method, path = 'ordinary_read', 'GET', '/entities?page_size=30&kind=data'
            elif op in (3, 4):
                category, method, path = 'ordinary_read', 'GET', '/search?q=' + ('项目字段000' if op == 3 else 'LOAD-000001')
            elif op == 5:
                category, method, path = 'ordinary_read', 'GET', '/graph?view=focus&node=LOAD-000001'
            elif op in (6, 7):
                category, method, path = 'file_read', 'GET', '/materials/' + docs[actor.workspace]['material_id'] + '/download'
            else:
                category, method, path = 'write', 'POST', '/entities'
                kwargs['json'] = {'id': f'LOAD-W-{index}-{sequence}', 'kind': 'data', 'title': f'LOAD/{actor.workspace}/新增字段{sequence}',
                                  'summary': '并发写入验证', 'domain': '项目管理', 'payload': {'definition': '负载测试生成'}}
            began = time.perf_counter()
            error = None
            size, status = 0, None
            try:
                result = await client.request(method, '/api' + path, **kwargs)
                status, size = result.status_code, len(result.content)
                if status != 200:
                    error = 'http_' + str(status)
                elif category == 'file_read':
                    if hashlib.sha256(result.content).hexdigest() != docs[actor.workspace]['file_sha256']:
                        error = 'file_tenant_mismatch'
                else:
                    body = result.json()
                    if not isinstance(body, dict):
                        raise ValueError('Expected response object')
                    if category == 'hq_aggregate':
                        if body.get('partial'):
                            error = 'partial_federation'
                        elif path.startswith('/federation/overview') and body.get('totals', {}).get('assets', 0) < expected_assets:
                            error = 'incomplete_federation_overview'
                        elif path.startswith('/federation/search') and (body.get('total', 0) < expected_assets or not body.get('items')):
                            error = 'incomplete_federation_search'
                        elif path.startswith('/federation/graph') and not set(docs).issubset({node.get('tenant_id') for node in body.get('nodes', [])}):
                            error = 'incomplete_federation_graph'
                    if category == 'ordinary_read' and isinstance(body, dict):
                        if '/graph' not in path and not body.get('items'):
                            error = 'unexpected_empty_result'
                        for item in body.get('items', []):
                            if item.get('org_id') != actor.workspace or not item.get('title', '').startswith('LOAD/' + actor.workspace + '/'):
                                error = 'entity_tenant_mismatch'
                        if '/graph' in path:
                            if not body.get('nodes'):
                                error = 'unexpected_empty_graph'
                            focus = next((node for node in body.get('nodes', []) if node.get('id') == 'LOAD-000001'), None)
                            if not focus or focus.get('org_id') != actor.workspace or focus.get('label') != f'LOAD/{actor.workspace}/项目字段000001':
                                error = 'focus_node_tenant_mismatch'
                            for node in body.get('nodes', []):
                                if node.get('org_id') and node['org_id'] != actor.workspace:
                                    error = 'graph_tenant_mismatch'
                    if category == 'write':
                        if body.get('id') != kwargs['json']['id'] or body.get('org_id') != actor.workspace or body.get('title') != kwargs['json']['title']:
                            error = 'write_identity_mismatch'
                        else:
                            successful_writes += 1
            except httpx.TimeoutException:
                error = 'timeout'
            except httpx.RequestError as exc:
                error = type(exc).__name__
            except (ValueError, KeyError, TypeError, AttributeError):
                error = 'invalid_response'
            ended = time.perf_counter()
            if began >= start + warmup:
                samples.append((category, (ended - began) * 1000, status, error, method + ' ' + path.split('?', 1)[0]))
                response_bytes += size
                if error:
                    errors[category + ':' + error] += 1
            sequence += 1

    async def monitor():
        nonlocal peak_rss, observed_processes, mysql_peak_rss
        while time.perf_counter() < stop:
            stats = process_stats(box.process.pid)
            peak_rss = max(peak_rss, stats['rss_kib'])
            observed_processes = max(observed_processes, stats['observed_processes'])
            resource_errors.update(stats['errors'])
            if mysql_pid:
                mysql_peak_rss = max(mysql_peak_rss, process_stats(mysql_pid)['rss_kib'])
            await asyncio.sleep(1)

    tasks = [asyncio.create_task(worker(index, actor, clients[index])) for index, actor in enumerate(actors)]
    start = time.perf_counter()
    stop = start + warmup + duration
    ready.set()
    tracking = asyncio.create_task(monitor())
    try:
        await asyncio.gather(*tasks)
        await tracking
    finally:
        for client in clients:
            await client.aclose()
    elapsed = time.perf_counter() - start - warmup
    groups = defaultdict(list)
    endpoint_groups = defaultdict(list)
    for category, latency, status, error, endpoint in samples:
        groups[category].append(latency)
        endpoint_groups[endpoint].append(latency)
    results = {category: {'requests': len(values), 'p50_ms': percentile(values, .5), 'p95_ms': percentile(values, .95),
                          'p99_ms': percentile(values, .99), 'max_ms': round(max(values), 3),
                          'throughput_rps': round(len(values) / elapsed, 3)} for category, values in groups.items()}
    all_latencies = [sample[1] for sample in samples]
    thresholds = {'ordinary_read': 500, 'file_read': 500, 'write': 1000, 'hq_aggregate': 2000}
    acceptance = {category: {'limit_p95_ms': limit, 'passed': results.get(category, {}).get('p95_ms', float('inf')) <= limit}
                  for category, limit in thresholds.items()}
    acceptance['hq_each_endpoint'] = {'limit_p95_ms': 2000, 'passed': all(percentile(values, .95) <= 2000 for name, values in endpoint_groups.items() if name.startswith('GET /federation/'))}
    acceptance['unprofiled'] = {'passed': not bool(os.getenv('ONTOLOGY_QA_PROFILE'))}
    acceptance['errors'] = {'count': sum(errors.values()), 'passed': not errors}
    acceptance['steady_duration'] = {'seconds': duration, 'passed': duration >= 60}
    final_stats = process_stats(box.process.pid)
    mysql_final = process_stats(mysql_pid) if mysql_pid else None
    resource_errors.update(final_stats['errors'])
    return {'result': 'passed' if all(x['passed'] for x in acceptance.values()) else 'failed',
            'steady_duration_seconds': duration, 'warmup_seconds': warmup, 'measured_wall_seconds_including_drain': round(elapsed, 3),
            'requests': len(samples), 'throughput_rps': round(len(samples) / elapsed, 3),
            'latency_ms': {'p50': percentile(all_latencies, .5), 'p95': percentile(all_latencies, .95), 'p99': percentile(all_latencies, .99)},
            'groups': results, 'errors': dict(errors), 'status_codes': dict(Counter(str(x[2]) for x in samples)),
            'endpoints': {name: {'requests': len(values), 'p50_ms': percentile(values, .5), 'p95_ms': percentile(values, .95), 'p99_ms': percentile(values, .99)} for name, values in endpoint_groups.items()},
            'hq_endpoints': {name.removeprefix('GET '): {'samples': len(values), 'p50_ms': percentile(values, .5),
                                     'p95_ms': percentile(values, .95), 'p99_ms': percentile(values, .99),
                                     'tail_estimate_note': 'Fewer than 100 samples: p99 is close to the observed maximum.' if len(values) < 100 else ''}
                              for name, values in endpoint_groups.items() if name.startswith('GET /federation/')},
            'successful_writes_including_warmup': successful_writes,
            'response_bytes': response_bytes, 'api_peak_rss_mib': round(peak_rss / 1024, 2) if peak_rss else None,
            'api_cpu_seconds_including_warmup': round(final_stats['cpu_seconds'] - cpu_start, 3) if final_stats['observed_processes'] else None,
            'mysql_resources_including_warmup': {'pid': mysql_pid, 'cpu_seconds': round(mysql_final['cpu_seconds'] - mysql_started['cpu_seconds'], 3), 'peak_rss_mib': round(mysql_peak_rss / 1024, 2), 'errors': sorted(set(mysql_started['errors'] + mysql_final['errors']))} if mysql_started and mysql_final else None,
            'cgroup_cpu_delta_including_warmup': {key: value - cgroup_started.get(key, 0) for key, value in cgroup_cpu().items()},
            'resource_observability': {'max_observed_processes': observed_processes, 'expected_processes_at_least': box.workers + (1 if box.workers > 1 else 0), 'errors': sorted(resource_errors)},
            'acceptance': acceptance}


def run(box, args, report):
    admin = Session(box, box.admin_name, box.admin_password)
    admin.login()
    tenants = [f'sub-{i + 1:02d}' for i in range(args.tenants)]
    primary, docs = [], {}
    for tenant in tenants:
        actor = box.user(admin, tenant, 'modeler', 'load-' + tenant)
        docs[tenant] = seed_volume(box, actor, args.entities)
        primary.append(actor)
    actors = list(primary)
    while len(actors) < args.sessions - 1:
        base = primary[(len(actors) - len(primary)) % len(primary)]
        session = Session(box, base.username, base.password)
        session.login()
        actors.append(session)
    hq = box.user(admin, 'hq', 'modeler', 'load-hq')
    actors.append(hq)
    assert len({actor.client.cookies.get('ontology_session') for actor in actors}) == len(actors), 'Every active actor must have a distinct real session cookie'
    report.update({'started_at': datetime.now(timezone.utc).isoformat(), 'hardware': hardware(box.process.pid, box.workers),
              'database_version': box.sql('SELECT VERSION() AS version')[0]['version'],
              'database_max_connections': box.sql('SELECT @@max_connections AS n')[0]['n'],
              'connection_pool_settings': {'business_per_worker': int(box.env.get('ONTOLOGY_DB_POOL_SIZE', '16')), 'control_per_worker': 16, 'note': 'Per-process pools; separate worker and maintenance processes require additional database capacity.'},
              'backend_snapshot_sha256': box.source_sha256,
              'active_sessions': len(actors), 'distinct_session_cookies_verified': True, 'institution_tenants': args.tenants, 'hq_sessions': 1,
              'initial_entities_per_tenant': args.entities, 'initial_business_entities': args.entities * args.tenants,
              'initial_relations': (args.entities // 2) * args.tenants,
              'files': len(docs), 'file_bytes_total': sum(x['file_bytes'] for x in docs.values()),
              'identity': 'Real password login for each session; forced initial password change; no identity mocks',
              'mix': f'{len(actors) - 1} institution sessions cycle 60% reads (list/search/graph), 20% file reads, 20% creates; one HQ session cycles aggregate overview/search/graph. Closed-loop no think time. Setup and warmup excluded.',
              'setup': 'Synthetic rows inserted directly only into test-owned tenant databases; measured writes use real API and CSRF.'})
    if args.start_signal:
        print(json.dumps({'stage': 'fixtures_ready', 'waiting_for': str(args.start_signal)}), flush=True)
        deadline = time.monotonic() + 300
        while not args.start_signal.exists():
            if time.monotonic() > deadline:
                raise RuntimeError('Timed out waiting for coordinated load start signal')
            time.sleep(.2)
    report['started_at'] = datetime.now(timezone.utc).isoformat()
    print(json.dumps({'stage': 'steady_load_start', 'sessions': len(actors), 'tenants': args.tenants,
                      'duration': args.duration, 'warmup': args.warmup, 'output': str(args.output)}), flush=True)
    report.update(asyncio.run(exercise(box, actors, docs, args.duration, args.warmup, args.entities * args.tenants, args.mysql_pid)))
    report['final_business_entities'] = sum(box.sql("SELECT COUNT(*) AS n FROM entities WHERE kind<>'organization'", database=box.tenant_db(tenant))[0]['n'] for tenant in tenants)
    report['acceptance']['target_scale'] = {'tenants': args.tenants, 'sessions': args.sessions, 'entities_per_tenant': args.entities,
                                             'passed': args.tenants >= 20 and args.sessions >= 50 and args.entities >= 1000}
    report['acceptance']['writes_persisted'] = {'passed': report['final_business_entities'] == args.entities * args.tenants + len(docs) + report['successful_writes_including_warmup']}
    report['result'] = 'passed' if all(x['passed'] for x in report['acceptance'].values()) else 'failed'
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8878)
    parser.add_argument('--duration', type=int, default=90)
    parser.add_argument('--warmup', type=int, default=10)
    parser.add_argument('--tenants', type=int, default=20)
    parser.add_argument('--sessions', type=int, default=50)
    parser.add_argument('--entities', type=int, default=1000)
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--start-signal', type=Path, help='Optional file gate after fixture setup, before warmup')
    parser.add_argument('--mysql-pid', type=int, default=0, help='Optional local MySQL process PID for resource measurement only')
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'work' / 'load-report.json')
    args = parser.parse_args()
    if not 1 <= args.tenants <= 37 or args.sessions < args.tenants + 1 or min(args.duration, args.entities, args.workers) <= 0 or args.warmup < 0:
        parser.error('Invalid tenant/session/duration/entity parameters')
    report = {'result': 'failed'}
    try:
        with Sandbox(args.port, args.output, workers=args.workers) as box:
            run(box, args, report)
    except Exception as exc:
        report['result'] = 'failed'
        report['fatal_error'] = str(exc)
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps({'result': report['result'], 'report': str(args.output)}, ensure_ascii=False))
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
