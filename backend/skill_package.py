"""Bounded Agent Skills text packages; parsing never grants or executes authority.

Agent Skills frontmatter follows the official specification. Platform manifests
are optional JSON resources and are governed by the calling domain service.
Archive operations stay in memory and never extract package paths to disk.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import stat
import struct
import unicodedata
import zipfile
from typing import Any

import yaml

MAX_FILES = 64
MAX_FILE_BYTES = 256 * 1024
MAX_PACKAGE_BYTES = 2 * 1024 * 1024
MAX_ZIP_BYTES = 2 * 1024 * 1024
MAX_PATH_LENGTH = 240
MAX_PATH_DEPTH = 8
MAX_FRONTMATTER_BYTES = 16 * 1024
MAX_ZIP_ENTRIES = 128
MAX_COMPRESSION_RATIO = 100
FRONTMATTER_FIELDS = frozenset({'name', 'description', 'license', 'compatibility', 'metadata', 'allowed-tools'})
CONVENTIONAL_ROOTS = frozenset({'scripts', 'references', 'refs', 'assets'})
ROOT_FILES = frozenset({'SKILL.md', 'LICENSE', 'LICENSE.txt', 'LICENSE.md', 'README.md'})
SECRET_NAMES = frozenset({'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519', 'credentials.json', 'source-credentials.json'})
SECRET_SUFFIXES = frozenset({'.pem', '.key', '.p12', '.pfx', '.keystore'})
# Detection is deliberately narrow and does not claim to replace enterprise DLP.
_SECRET_PATTERNS = (
    re.compile(r'-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----'),
    re.compile(r'\bonto_app_[A-Za-z0-9_-]{43}\b'),
    re.compile(r'\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b'),
    re.compile(r'\bgithub_pat_[A-Za-z0-9_]{50,}\b'),
    re.compile(r'\bAKIA[0-9A-Z]{16}\b'),
    re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}\b'),
)
_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


class SkillPackageError(ValueError):
    """Fixed safe diagnostic, never the rejected contents or credential value."""
    def __init__(self, code: str, message: str, path: str = ''):
        super().__init__(message)
        self.code, self.message, self.path = code, message, path

    def issue(self):
        return {'code': self.code, 'path': self.path, 'message': self.message}


def _error(code, message, path=''):
    raise SkillPackageError(code, message, path)


def _secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


def _safe_display_path(value: str) -> str:
    # A path can itself contain a credential; it must not become error/audit text.
    if not isinstance(value, str) or _secret(value) or _CONTROL.search(value):
        return ''
    return value[:MAX_PATH_LENGTH] if len(value) <= MAX_PATH_LENGTH else ''


def _path_key(path: str) -> str:
    return unicodedata.normalize('NFKC', path).casefold()


def _validate_path(path: str, *, directory=False, archive=False) -> str:
    shown = _safe_display_path(path)
    if not isinstance(path, str) or not path or len(path) > MAX_PATH_LENGTH + (65 if archive else 0) or _secret(path):
        _error('INVALID_PATH', '文件路径无效或超出允许范围。', shown)
    if directory:
        path = path[:-1] if path.endswith('/') else path
    if not path or path.startswith('/') or '\\' in path or ':' in path or '%' in path or _CONTROL.search(path):
        _error('INVALID_PATH', '文件路径必须是安全的相对路径。', shown)
    parts = path.split('/')
    if len(parts) > MAX_PATH_DEPTH + (1 if archive else 0):
        _error('PATH_DEPTH', '文件路径最多允许8层。', shown)
    for part in parts:
        canonical = unicodedata.normalize('NFKC', part)
        try:
            encoded = part.encode('utf-8', 'strict')
        except UnicodeError:
            _error('INVALID_PATH', '文件路径必须是有效UTF-8文本。', shown)
        if (not part or part in {'.', '..'} or part.startswith('.') or part != part.strip() or part.endswith('.')
                or len(encoded) > 255 or canonical.startswith('.') or canonical in {'', '.', '..'}
                or any(char in canonical for char in '/\\:%<>"|?*') or _CONTROL.search(canonical)
                or any(unicodedata.category(char).startswith('C') for char in part)):
            _error('INVALID_PATH', '文件路径包含隐藏、控制或不安全片段。', shown)
        lower = canonical.casefold()
        if lower in SECRET_NAMES or any(lower.endswith(suffix) for suffix in SECRET_SUFFIXES):
            _error('SECRET_FILE', '技能包不能包含凭据文件或私钥。', shown)
        # Avoid device-file interpretation on Windows consumers of exported ZIPs.
        basename = lower.split('.', 1)[0].rstrip(' ')
        if basename in {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}:
            _error('INVALID_PATH', '文件路径使用了保留设备名称。', shown)
    return path


def _bounded_files(files: Any) -> tuple[dict[str, str], int]:
    if not isinstance(files, dict) or not files or len(files) > MAX_FILES:
        _error('FILE_COUNT', '技能包须包含1～64个文本文件。')
    result, seen, total = {}, set(), 0
    for path, text in files.items():
        path = _validate_path(path)
        key = _path_key(path)
        if key in seen:
            _error('DUPLICATE_PATH', '技能包存在重复或规范化后冲突的文件路径。', _safe_display_path(path))
        seen.add(key)
        if not isinstance(text, str):
            _error('TEXT_REQUIRED', '本版技能包只接受UTF-8文本文件。', path)
        try:
            size = len(text.encode('utf-8', 'strict'))
        except UnicodeError:
            _error('TEXT_REQUIRED', '文件不是有效的UTF-8文本。', path)
        if size > MAX_FILE_BYTES:
            _error('FILE_SIZE', '单文件不能超过256KiB。', path)
        total += size
        if total > MAX_PACKAGE_BYTES:
            _error('PACKAGE_SIZE', '技能包文本总大小不能超过2MiB。')
        if _CONTROL.search(text):
            _error('TEXT_REQUIRED', '文件包含二进制或不支持的控制字符。', path)
        if _secret(text):
            _error('CREDENTIAL_DETECTED', '检测到疑似访问令牌或私钥，请移除后再保存。', path)
        result[path] = text
    for path in seen:
        if any('/'.join(path.split('/')[:i]) in seen for i in range(1, len(path.split('/')))):
            _error('PATH_CONFLICT', '同一路径不能同时作为文件和目录。')
    return result, total


def package_digest(files: dict[str, str]) -> str:
    """Stable content digest over exact UTF-8 bytes, independent of ZIP metadata."""
    bounded, _ = _bounded_files(files)
    digest = hashlib.sha256(b'ontology-one-skill-package-v1\0')
    for path in sorted(bounded):
        name, body = path.encode('utf-8'), bounded[path].encode('utf-8')
        digest.update(struct.pack('>I', len(name)) + name + struct.pack('>I', len(body)) + body)
    return digest.hexdigest()


class _FrontmatterLoader(yaml.BaseLoader):
    """Like the official parser, all YAML scalars retain their string values."""
    def construct_mapping(self, node, deep=False):
        if not isinstance(node, yaml.MappingNode):
            _error('FRONTMATTER_TYPE', 'SKILL.md头部须为YAML键值映射。', 'SKILL.md')
        result = {}
        for key_node, value_node in node.value:
            if not isinstance(key_node, yaml.ScalarNode):
                _error('FRONTMATTER_KEY', 'YAML键必须是文本。', 'SKILL.md')
            key = self.construct_scalar(key_node)
            if key in result or key == '<<':
                _error('FRONTMATTER_DUPLICATE', 'YAML不能含重复键或合并键。', 'SKILL.md')
            result[key] = self.construct_object(value_node, deep=deep)
        return result


_FrontmatterLoader.add_constructor('tag:yaml.org,2002:map', _FrontmatterLoader.construct_mapping)


def _frontmatter(text: str) -> tuple[dict, str]:
    lines = text.replace('\r\n', '\n').split('\n')
    if not lines or lines[0].lstrip('\ufeff') != '---':
        _error('FRONTMATTER_MISSING', 'SKILL.md必须以YAML头部开始。', 'SKILL.md')
    end = next((i for i, line in enumerate(lines[1:], 1) if line == '---'), None)
    if end is None:
        _error('FRONTMATTER_UNCLOSED', 'SKILL.md的YAML头部缺少结束分隔线。', 'SKILL.md')
    header = '\n'.join(lines[1:end])
    if len(header.encode()) > MAX_FRONTMATTER_BYTES:
        _error('FRONTMATTER_SIZE', 'YAML头部不能超过16KiB。', 'SKILL.md')
    try:
        count, depth = 0, 0
        for token in yaml.scan(header, Loader=_FrontmatterLoader):
            count += 1
            if count > 2048:
                _error('FRONTMATTER_COMPLEXITY', 'YAML头部结构过于复杂。', 'SKILL.md')
            if isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken, yaml.tokens.TagToken)):
                _error('FRONTMATTER_UNSAFE', 'YAML不接受别名、锚点或自定义标签。', 'SKILL.md')
        for event in yaml.parse(header, Loader=_FrontmatterLoader):
            if isinstance(event, (yaml.events.MappingStartEvent, yaml.events.SequenceStartEvent)):
                depth += 1
                if depth > 8:
                    _error('FRONTMATTER_COMPLEXITY', 'YAML头部嵌套过深。', 'SKILL.md')
            elif isinstance(event, (yaml.events.MappingEndEvent, yaml.events.SequenceEndEvent)):
                depth -= 1
        metadata = yaml.load(header, Loader=_FrontmatterLoader)
    except yaml.YAMLError:
        _error('FRONTMATTER_SYNTAX', 'SKILL.md的YAML头部语法无效。', 'SKILL.md')
    if not isinstance(metadata, dict):
        _error('FRONTMATTER_TYPE', 'SKILL.md头部须为YAML键值映射。', 'SKILL.md')
    return metadata, '\n'.join(lines[end + 1:]).strip()


def _name_valid(name):
    return (isinstance(name, str) and 1 <= len(name) <= 64 and name == name.strip()
            and name == name.lower() and not name.startswith('-') and not name.endswith('-')
            and '--' not in name and all(char.isalnum() or char == '-' for char in name)
            and unicodedata.normalize('NFKC', name) == name)


def _validate_frontmatter(metadata, expected_name):
    if _secret(json.dumps(metadata, ensure_ascii=False)):
        _error('CREDENTIAL_DETECTED', 'YAML头部不能包含访问令牌或私钥。', 'SKILL.md')
    if set(metadata) - FRONTMATTER_FIELDS:
        _error('FRONTMATTER_FIELD', 'YAML头部包含Agent Skills规范之外的字段，请放入metadata。', 'SKILL.md')
    if not _name_valid(metadata.get('name')):
        _error('SKILL_NAME', '技能名称须为1～64位小写字母、数字或连字符，不能首尾或连续使用连字符。', 'SKILL.md')
    _validate_path(metadata['name'])
    if expected_name is not None and metadata['name'] != expected_name:
        _error('SKILL_DIRECTORY', '包目录名称必须与SKILL.md的name一致。', 'SKILL.md')
    description = metadata.get('description')
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        _error('SKILL_DESCRIPTION', '技能说明须为1～1024个字符。', 'SKILL.md')
    for field, limit in [('license', 1024), ('compatibility', 500), ('allowed-tools', 2048)]:
        if field in metadata and (not isinstance(metadata[field], str) or not metadata[field].strip() or len(metadata[field]) > limit):
            _error('FRONTMATTER_VALUE', '许可、兼容要求或工具声明的类型或长度无效。', 'SKILL.md')
    extra = metadata.get('metadata', {})
    if (not isinstance(extra, dict) or len(extra) > 64
            or any(not isinstance(k, str) or not k.strip() or len(k) > 200 or not isinstance(v, str) or len(v) > 2048 for k, v in extra.items())):
        _error('FRONTMATTER_METADATA', 'metadata须为有界的文本键值映射。', 'SKILL.md')


def _json_resource(text, path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _error('JSON_DUPLICATE', 'JSON文件不能包含重复键。', path)
            result[key] = value
        return result

    def constant(_):
        _error('JSON_VALUE', 'JSON文件不接受非有限数字。', path)

    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
        pending, count = [(value, 1)], 0
        while pending:
            item, depth = pending.pop()
            count += 1
            if depth > 16 or count > 20000:
                _error('JSON_COMPLEXITY', 'JSON文件嵌套或元素数量超出允许范围。', path)
            if isinstance(item, dict):
                if any(_secret(key) for key in item):
                    _error('CREDENTIAL_DETECTED', 'JSON文件不能包含访问令牌或私钥。', path)
                pending.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list):
                pending.extend((child, depth + 1) for child in item)
            elif isinstance(item, str) and _secret(item):
                _error('CREDENTIAL_DETECTED', 'JSON文件不能包含访问令牌或私钥。', path)
            elif isinstance(item, float) and not math.isfinite(item):
                _error('JSON_VALUE', 'JSON文件不接受非有限数字。', path)
        return value
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        if isinstance(exc, SkillPackageError):
            raise
        _error('JSON_SYNTAX', 'JSON文件语法无效。', path)


def validate_files(files: dict[str, str], expected_name: str | None = None) -> dict:
    """Return safe diagnostics; none of the package's instructions are executed."""
    report = {'valid': False, 'errors': [], 'warnings': [], 'frontmatter': {}, 'body': '',
              'digest': '', 'file_count': 0, 'total_bytes': 0}
    try:
        bounded, size = _bounded_files(files)
        report.update(file_count=len(bounded), total_bytes=size, digest=package_digest(bounded))
        if 'SKILL.md' not in bounded:
            _error('SKILL_FILE_MISSING', '技能包缺少根目录SKILL.md。')
        metadata, body = _frontmatter(bounded['SKILL.md'])
        _validate_frontmatter(metadata, expected_name)
        report.update(frontmatter=metadata, body=body)
        if len(bounded['SKILL.md'].splitlines()) > 500:
            report['warnings'].append({'code': 'LONG_INSTRUCTIONS', 'path': 'SKILL.md', 'message': '建议把超过500行的详细资料移入references。'})
        if not body:
            report['warnings'].append({'code': 'EMPTY_INSTRUCTIONS', 'path': 'SKILL.md', 'message': '技能正文为空，发布前请补齐操作步骤。'})
        if metadata.get('allowed-tools'):
            report['warnings'].append({'code': 'TOOLS_DECLARATION_ONLY', 'path': 'SKILL.md', 'message': 'allowed-tools仅为兼容声明，不授予平台或源系统权限。'})
        manifest = metadata.get('metadata', {}).get('ontology-one.manifest')
        if manifest:
            _validate_path(manifest)
            if manifest not in bounded or not manifest.lower().endswith('.json'):
                _error('MANIFEST_REFERENCE', '平台清单引用须指向包内已有JSON文件。', 'SKILL.md')
        for path, content in bounded.items():
            if path.lower().endswith('.json'):
                try:
                    _json_resource(content, path)
                except SkillPackageError as exc:
                    report['errors'].append(exc.issue())
            elif path.startswith('scripts/') and path.endswith('.py'):
                try:
                    compile(content, '<skill-script>', 'exec', dont_inherit=True)
                except (SyntaxError, ValueError, RecursionError, MemoryError):
                    report['errors'].append({'code': 'PYTHON_SYNTAX', 'path': path, 'message': 'Python脚本语法无效；校验不会执行脚本。'})
            if '/' in path and path.split('/', 1)[0] not in CONVENTIONAL_ROOTS:
                report['warnings'].append({'code': 'NONSTANDARD_DIRECTORY', 'path': path, 'message': '建议将脚本、参考资料和资源分别归入scripts、references、assets。'})
            elif '/' not in path and path not in ROOT_FILES:
                report['warnings'].append({'code': 'NONSTANDARD_ROOT_FILE', 'path': path, 'message': '这是附加根文件，运行加载由已审核清单决定。'})
        report['valid'] = not report['errors']
    except SkillPackageError as exc:
        report['errors'].append(exc.issue())
    return report



def sync_manifest(files: dict[str, str], portable_manifest: dict) -> dict[str, str]:
    """Synchronize portable hints without treating any package field as a grant.

    The domain service selects the portable fields. Other resources and the
    Markdown body retain their exact content; only frontmatter and the dedicated
    manifest resource are rewritten. Validation remains a separate operation so
    an editor can receive diagnostics for incomplete draft instructions/scripts.
    """
    bounded, _ = _bounded_files(files)
    if 'SKILL.md' not in bounded:
        _error('SKILL_FILE_MISSING', '技能包缺少根目录SKILL.md。')
    metadata, _ = _frontmatter(bounded['SKILL.md'])
    _validate_frontmatter(metadata, None)
    if not isinstance(portable_manifest, dict):
        _error('JSON_VALUE', '平台清单须为JSON对象。', 'assets/ontology-one.json')
    try:
        manifest = json.dumps(portable_manifest, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2) + '\n'
    except (TypeError, ValueError, RecursionError):
        _error('JSON_VALUE', '平台清单包含不支持的JSON值。', 'assets/ontology-one.json')
    if len(manifest.encode('utf-8')) > MAX_FILE_BYTES:
        _error('FILE_SIZE', '平台清单不能超过256KiB。', 'assets/ontology-one.json')
    _json_resource(manifest, 'assets/ontology-one.json')
    metadata['metadata'] = {**metadata.get('metadata', {}), 'ontology-one.manifest': 'assets/ontology-one.json'}
    original_lines = bounded['SKILL.md'].splitlines(keepends=True)
    closing = next(i for i, line in enumerate(original_lines[1:], 1) if line.rstrip('\r\n') == '---')
    body = ''.join(original_lines[closing + 1:])
    header = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False, default_flow_style=False).rstrip('\n')
    result = {**bounded, 'SKILL.md': '---\n' + header + '\n---\n' + body,
              'assets/ontology-one.json': manifest}
    _bounded_files(result)
    return result


def _zip_directory(raw: bytes):
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_ZIP_BYTES:
        _error('ZIP_SIZE', 'ZIP文件不能为空或超过2MiB。')
    position = raw.rfind(b'PK\x05\x06', max(0, len(raw) - 65557))
    if position < 0 or position + 22 > len(raw):
        _error('ZIP_FORMAT', 'ZIP结构无效。')
    signature, disk, directory_disk, disk_entries, entries, directory_size, directory_offset, comment = struct.unpack_from('<4s4H2IH', raw, position)
    if position + 22 + comment != len(raw) or disk or directory_disk or disk_entries != entries:
        _error('ZIP_FORMAT', '不接受多卷ZIP或追加内容。')
    if entries == 65535 or directory_size == 0xFFFFFFFF or directory_offset == 0xFFFFFFFF:
        _error('ZIP_FORMAT', '本版不接受ZIP64归档。')
    if not 1 <= entries <= MAX_ZIP_ENTRIES or directory_offset + directory_size != position:
        _error('ZIP_FORMAT', 'ZIP条目数或目录结构超出允许范围。')
    if not raw.startswith(b'PK\x03\x04'):
        _error('ZIP_FORMAT', 'ZIP不得包含自解压程序或前置内容。')


def read_zip(raw: bytes) -> dict[str, str]:
    """Import a flat package or one named root folder, without disk extraction."""
    _zip_directory(raw)
    result, names, files, directories = {}, set(), [], set()
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ZIP_ENTRIES:
                _error('FILE_COUNT', 'ZIP条目过多。')
            total = 0
            for info in entries:
                original = info.orig_filename
                is_directory = info.is_dir()
                name = _validate_path(original, directory=is_directory, archive=True)
                key = _path_key(name)
                if key in names:
                    _error('DUPLICATE_PATH', 'ZIP存在重复或规范化后冲突的路径。', _safe_display_path(name))
                names.add(key)
                mode = (info.external_attr >> 16) & 0xFFFF
                kind = stat.S_IFMT(mode)
                if (kind not in {0, stat.S_IFREG, stat.S_IFDIR}
                        or (kind == stat.S_IFDIR and not is_directory) or (kind == stat.S_IFREG and is_directory)):
                    _error('ZIP_LINK', 'ZIP只能包含普通文件和目录，不能包含链接或设备。', _safe_display_path(name))
                if info.flag_bits & (1 | 0x20 | 0x40) or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    _error('ZIP_ENCODING', 'ZIP不能加密或使用不支持的压缩方式。')
                if is_directory:
                    if info.file_size:
                        _error('ZIP_FORMAT', 'ZIP目录条目不能包含内容。')
                    directories.add(key)
                    continue
                if len(files) >= MAX_FILES:
                    _error('FILE_COUNT', '技能包最多包含64个文件。')
                if info.file_size > MAX_FILE_BYTES or info.file_size < 0:
                    _error('FILE_SIZE', 'ZIP单文件不能超过256KiB。', _safe_display_path(name))
                if info.file_size > max(1, info.compress_size) * MAX_COMPRESSION_RATIO:
                    _error('ZIP_RATIO', 'ZIP压缩比超过允许范围。', _safe_display_path(name))
                total += info.file_size
                if total > MAX_PACKAGE_BYTES:
                    _error('PACKAGE_SIZE', 'ZIP解压文本总大小不能超过2MiB。')
                with archive.open(info) as stream:
                    data = stream.read(MAX_FILE_BYTES + 1)
                if len(data) > MAX_FILE_BYTES or len(data) != info.file_size:
                    _error('FILE_SIZE', 'ZIP文件实际大小与声明不符。', _safe_display_path(name))
                try:
                    text = data.decode('utf-8', 'strict')
                except UnicodeError:
                    _error('TEXT_REQUIRED', '本版技能包只接受UTF-8文本。', _safe_display_path(name))
                files.append((name, text))
            file_names = {_path_key(name) for name, _ in files}
            if file_names & directories or any('/'.join(key.split('/')[:i]) in file_names for key in names for i in range(1, len(key.split('/')))):
                _error('PATH_CONFLICT', 'ZIP路径不能同时作为文件和目录。')
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, UnicodeError, EOFError, OSError, ValueError) as exc:
        if isinstance(exc, SkillPackageError):
            raise
        _error('ZIP_FORMAT', 'ZIP内容损坏或编码不受支持。')
    top_skills = [name for name, _ in files if name in {'SKILL.md', 'skill.md'}]
    expected_name = None
    if top_skills:
        if len(top_skills) != 1:
            _error('DUPLICATE_PATH', '技能包只能有一个根技能说明。')
        result = dict(files)
    else:
        roots = {name.split('/', 1)[0] for name, _ in files}
        if len(roots) != 1 or not files or any('/' not in name for name, _ in files):
            _error('SKILL_FILE_MISSING', 'ZIP须为一个技能目录或直接包含SKILL.md。')
        expected_name = next(iter(roots))
        if any(key != _path_key(expected_name) and not key.startswith(_path_key(expected_name) + '/') for key in names):
            _error('SKILL_DIRECTORY', 'ZIP只能包含同一个技能根目录。')
        result = {name.split('/', 1)[1]: text for name, text in files}
    if 'skill.md' in result and 'SKILL.md' not in result:
        result['SKILL.md'] = result.pop('skill.md')
    report = validate_files(result, expected_name=expected_name)
    if not report['valid']:
        issue = report['errors'][0]
        _error(issue['code'], issue['message'], issue['path'])
    return result


def write_zip(files: dict[str, str]) -> bytes:
    """Deterministic portable archive rooted at the declared Agent Skills name."""
    report = validate_files(files)
    if not report['valid']:
        issue = report['errors'][0]
        _error(issue['code'], issue['message'], issue['path'])
    name = report['frontmatter']['name']
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(files):
            info = zipfile.ZipInfo(name + '/' + path, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            # Stored entries guarantee an exported bounded package re-imports
            # even when its legitimate template text is highly repetitive.
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, files[path].encode('utf-8'))
    data = output.getvalue()
    if len(data) > MAX_ZIP_BYTES:
        _error('ZIP_SIZE', '导出ZIP含路径开销后超过2MiB，请缩小资源内容。')
    return data
