"""Restore a DEMO MySQL workspace backup into an EMPTY database.

Example:
  MYSQL_HOST=127.0.0.1 MYSQL_USER=root MYSQL_PASSWORD=... \
  python scripts/restore_backup.py backup.zip --database ontology_one_restore --yes

The application must be stopped. Existing databases are never overwritten.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.db import connect, initialize, mysql_config  # noqa: E402


def restore_database(snapshot, database):
    if snapshot.get('format') != 'ontology-mysql-workspace-v1' or snapshot.get('database') != 'MySQL':
        raise RuntimeError('这不是受支持的Ontology One DEMO MySQL 工作区快照。')
    tables = snapshot.get('tables')
    if not isinstance(tables, dict) or not tables:
        raise RuntimeError('备份中没有可恢复的数据表。')
    if any(not re.fullmatch(r'[A-Za-z0-9_]+', table) for table in tables):
        raise RuntimeError('备份包含无效数据表名称。')
    with closing(connect()) as conn:
        existing = conn.execute(
            'SELECT count(*) FROM information_schema.tables WHERE table_schema=DATABASE()'
        ).fetchone()[0]
    if existing:
        raise RuntimeError(f'目标数据库 {database} 不是空库（已有 {existing} 张表）。为避免覆盖业务数据，本次未恢复。')

    # Create the exact DEMO table structures. Seed rows are replaced by the snapshot
    # below, so no demonstration or user record from initialization survives.
    initialize()
    restored = 0
    with closing(connect()) as conn, conn:
        current = {row[0] for row in conn.execute(
            'SELECT table_name FROM information_schema.tables WHERE table_schema=DATABASE()')}
        missing = sorted(set(tables) - current)
        if missing:
            raise RuntimeError('当前程序缺少备份所需数据表：' + '、'.join(missing))
        conn.execute('SET FOREIGN_KEY_CHECKS=0')
        for table, rows in tables.items():
            if not isinstance(rows, list):
                raise RuntimeError(f'数据表 {table} 的备份格式不正确。')
            conn.execute(f'DELETE FROM `{table}`')
            if not rows:
                continue
            columns = list(rows[0])
            if not columns or any(not re.fullmatch(r'[A-Za-z0-9_]+', column) for column in columns):
                raise RuntimeError(f'数据表 {table} 包含无效字段。')
            if any(list(row) != columns for row in rows):
                raise RuntimeError(f'数据表 {table} 的字段顺序不一致。')
            names = ','.join(f'`{column}`' for column in columns)
            marks = ','.join('?' for _ in columns)
            values = [[row.get(column) for column in columns] for row in rows]
            for start in range(0, len(values), 500):
                conn.executemany(f'INSERT INTO `{table}`({names}) VALUES({marks})', values[start:start + 500])
            restored += len(rows)
        conn.execute('SET FOREIGN_KEY_CHECKS=1')
    return restored


def restore_files(archive):
    count = 0
    for info in archive.infolist():
        if info.is_dir() or not (info.filename.startswith('data/uploads/') or info.filename.startswith('data/source/')):
            continue
        relative = Path(info.filename)
        if '..' in relative.parts or relative.is_absolute():
            raise RuntimeError('备份包含不安全的文件路径。')
        target = ROOT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, target.open('wb') as output:
            while chunk := source.read(1024 * 1024):
                output.write(chunk)
        count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description='将Ontology One DEMO 备份恢复到空 MySQL 数据库。')
    parser.add_argument('backup', type=Path)
    parser.add_argument('--database', required=True, help='目标空数据库；不存在时会自动创建。')
    parser.add_argument('--yes', action='store_true', help='确认平台已停止，且目标数据库允许初始化。')
    parser.add_argument('--with-files', action='store_true', help='同时恢复上传原件与资料底座到当前项目 data 目录。')
    args = parser.parse_args()
    if not args.yes:
        parser.error('请确认已停止平台后添加 --yes。已有数据的数据库不会被覆盖。')
    if not re.fullmatch(r'[A-Za-z0-9_]+', args.database):
        parser.error('数据库名仅允许字母、数字和下划线。')
    if not args.backup.is_file():
        parser.error('备份文件不存在。')
    settings = mysql_config()
    os.environ.pop('ONTOLOGY_MYSQL_URL', None)
    for key in ('host', 'port', 'user', 'password'):
        os.environ['MYSQL_' + key.upper()] = str(settings[key])
    os.environ['MYSQL_DATABASE'] = args.database
    try:
        with zipfile.ZipFile(args.backup) as archive:
            with archive.open('data/mysql-workspace.json') as stream:
                snapshot = json.load(stream)
            rows = restore_database(snapshot, args.database)
            files = restore_files(archive) if args.with_files else 0
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
        raise SystemExit('备份压缩包不完整或格式不正确：' + str(exc))
    except Exception as exc:
        raise SystemExit('恢复未完成：' + str(exc))
    print(f'恢复完成：数据库 {args.database} 共写入 {rows:,} 行；恢复文件 {files:,} 个。')
    print('请使用相同的 MYSQL_HOST、MYSQL_PORT、MYSQL_USER、MYSQL_PASSWORD 和此 MYSQL_DATABASE 启动 DEMO。')


if __name__ == '__main__':
    main()
