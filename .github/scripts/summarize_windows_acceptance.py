"""Print bounded native-CI metadata without raw output or exception messages.

Report labels, error symbols, test identifiers and source paths must exist in
the checked-out application. Unknown text and all traceback source lines are
discarded. This helper does not import or execute the delivered application.
"""
from __future__ import annotations

import argparse
import ast
import builtins
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
LIMIT = 2 * 1024 * 1024
STATUSES = {'passed', 'failed', 'not_run', 'skipped'}
NUMBERS = {'exit_code', 'duration_ms', 'passed', 'failed', 'completed_tenants',
    'total_tenants', 'elapsed_ms', 'native_error', 'winerror', 'errno',
    'process_exit', 'exit_code', 'user_cpu_ms', 'kernel_cpu_ms',
    'process_read_bytes', 'process_write_bytes', 'stdin_bytes', 'stdout_bytes', 'stderr_bytes'}
REPORTS = ('windows-ci-report.json', 'windows-portable-smoke.json', 'windows-upgrade-smoke.json')
LOGS = ('native_units.log', 'ci-entry.stderr.log', 'portable_smoke.log', 'upgrade_smoke.log')


def contract(root):
    paths, labels, symbols, tests = {}, {'native_units', 'portable_smoke', 'upgrade_smoke', 'final_cleanup'}, set(), set()
    classes = {name for name, value in vars(builtins).items()
               if isinstance(value, type) and issubclass(value, BaseException)}
    for directory in ('backend', 'ops', 'scripts', 'tests'):
        for path in (root / directory).rglob('*.py'):
            if any(part in {'__pycache__', 'data', 'runtime', 'work'} for part in path.relative_to(root).parts):
                continue
            source = path.read_text(encoding='utf-8-sig')
            relative = path.relative_to(root).as_posix()
            paths[relative] = len(source.splitlines())
            tree = ast.parse(source)
            if directory == 'tests':
                tests.add(relative[:-3].replace('/', '.'))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if re.fullmatch(r'[A-Z][A-Z0-9_]{1,95}', node.value):
                        symbols.add(node.value)
                if isinstance(node, ast.ClassDef):
                    classes.add(node.name)
                    if directory == 'tests':
                        module = relative[:-3].replace('/', '.')
                        for child in node.body:
                            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.startswith('test_'):
                                tests.add(module + '.' + node.name + '.' + child.name)
                if isinstance(node, ast.Call) and node.args:
                    name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, 'id', None)
                    if name in {'check', 'record'} and isinstance(node.args[0], ast.Constant):
                        value = node.args[0].value
                        if isinstance(value, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,95}', value):
                            labels.add(value)
    return paths, labels, symbols, tests, classes


def read(path):
    with path.open('rb') as stream:
        value = stream.read(LIMIT + 1)
    if len(value) > LIMIT:
        raise ValueError('INPUT_LIMIT')
    return value.decode('utf-8-sig', errors='replace')


def number(value):
    return type(value) is int and -(2**31) <= value < 2**64


def metadata(value, symbols, classes):
    if not isinstance(value, dict):
        return {}
    result = {key: item for key, item in value.items() if key in NUMBERS and number(item)}
    for key in ('code', 'error'):
        item = value.get(key)
        if isinstance(item, str) and item in symbols:
            result[key] = item
        elif isinstance(item, str) and item in classes:
            result['exception'] = item
        elif isinstance(item, str) and item.startswith('UNEXPECTED_LAUNCH_EXIT_'):
            match = re.fullmatch(r'UNEXPECTED_LAUNCH_EXIT_(-?\d{1,10})(?:_([A-Z][A-Z0-9_]{1,95}))?', item)
            if match and (match[2] is None or match[2] in symbols):
                result[key] = item
    if isinstance(value.get('exception'), str) and value['exception'] in classes:
        result['exception'] = value['exception']
    for key in ('diagnostic', 'launcher_failure', 'process_counters'):
        nested = metadata(value.get(key), symbols, classes)
        if nested:
            result[key] = nested
    for key in ('mysql_codes', 'mysql_client_codes'):
        items = value.get(key)
        if isinstance(items, list):
            result[key] = ([item for item in items[:12]
                if isinstance(item, str) and re.fullmatch(r'MY-\d{6}', item)] if key == 'mysql_codes'
                else [item for item in items[:12] if type(item) is int and 0 < item < 65536])
    return result


def report_summary(value, labels, symbols, classes):
    if not isinstance(value, dict):
        return {'status': 'unavailable'}
    result = metadata(value, symbols, classes)
    if type(value.get('passed')) is bool:
        result['passed'] = value['passed']
    rows = value.get('checks', [])
    result['checks'] = []
    if isinstance(rows, list):
        for item in rows[:256]:
            if not isinstance(item, dict) or not isinstance(item.get('name'), str) or item['name'] not in labels:
                continue
            row = {'name': item['name'], **metadata(item, symbols, classes)}
            if isinstance(item.get('status'), str) and item['status'] in STATUSES:
                row['status'] = item['status']
            if type(item.get('passed')) is bool:
                row['passed'] = item['passed']
            result['checks'].append(row)
    return result


def log_summary(text, paths, tests, classes, symbols=None):
    symbols = set() if symbols is None else symbols
    result = {'failed_tests': [], 'frames': [], 'exception_types': [], 'error_codes': []}
    for line in text.splitlines():
        marker = line.find('{"native_cpu_limit_failure":')
        if marker >= 0:
            try:
                # unittest's verbose test name may precede flushed stdout on
                # the same redirected line. Only the structured suffix enters
                # metadata filtering; the surrounding text is never exported.
                value = json.loads(line[marker:])['native_cpu_limit_failure']
                result['cpu_limit_failure'] = metadata(value, symbols, classes)
            except Exception:
                pass
        match = re.fullmatch(r'Ran (\d{1,6}) tests? in [0-9.]+s', line)
        if match:
            result['tests_run'] = int(match[1])
        match = re.fullmatch(r'(OK|FAILED)(?: \(([^)]{0,120})\))?', line)
        if match:
            result['outcome'] = match[1]
            for name, count in re.findall(r'(failures|errors|skipped|expected failures|unexpected successes)=(\d{1,6})', match[2] or ''):
                result[name.replace(' ', '_')] = int(count)
        match = re.match(r'^(?:FAIL|ERROR): ([A-Za-z0-9_.]+) \(([A-Za-z0-9_.]+)\)', line)
        if match:
            identity = match[1] if match[1] in tests else (match[2] if match[2].endswith('.' + match[1]) else match[2] + '.' + match[1])
            if identity in tests and len(result['failed_tests']) < 32:
                result['failed_tests'].append(identity)
        match = re.fullmatch(r'  File "([^"]+)", line (\d{1,7}), in [A-Za-z0-9_<>]+', line)
        if match:
            raw = match[1].replace('\\', '/')
            for relative, maximum in paths.items():
                frame = {'source': relative, 'line': int(match[2])}
                if (raw == relative or raw.endswith('/' + relative)) and 0 < frame['line'] <= maximum:
                    if frame not in result['frames'] and len(result['frames']) < 64:
                        result['frames'].append(frame)
                    break
        match = re.fullmatch(r'((?:[A-Za-z][A-Za-z0-9_]*\.)*[A-Za-z][A-Za-z0-9_]*)(?:: ?(.*))?', line)
        if match:
            name = match[1].rsplit('.', 1)[-1]
            if name in classes:
                if name not in result['exception_types']:
                    result['exception_types'].append(name)
                if match[2] in symbols and match[2] not in result['error_codes']:
                    result['error_codes'].append(match[2])
    return result


def summarize(reports, root=ROOT):
    paths, labels, symbols, tests, classes = contract(root)
    result = {'schema_version': 1, 'raw_output_included': False, 'reports': {}, 'logs': {}}
    for name in REPORTS:
        try:
            result['reports'][name] = report_summary(json.loads(read(reports / name)), labels, symbols, classes)
        except Exception:
            result['reports'][name] = {'status': 'unavailable'}
    for name in LOGS:
        try:
            result['logs'][name] = log_summary(read(reports / name), paths, tests, classes, symbols)
        except Exception:
            result['logs'][name] = {'status': 'unavailable'}
    try:
        package = json.loads(read(reports.parent / 'windows-build-provenance.json'))['package']
        if re.fullmatch(r'[0-9a-f]{64}', package['sha256']) and number(package['bytes']):
            result['package'] = {'sha256': package['sha256'], 'bytes': package['bytes']}
    except Exception:
        pass
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reports', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = summarize(args.reports)
    except Exception:
        result = {'schema_version': 1, 'status': 'summary_unavailable', 'raw_output_included': False}
    print(json.dumps(result, ensure_ascii=True, separators=(',', ':')))


if __name__ == '__main__':
    main()
