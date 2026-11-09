"""Local MySQL workspace export. Model credentials are never included."""
import json
import shutil
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from .db import ROOT, connect, mysql_config, now

router = APIRouter()


@router.get('/system/release')
def release():
    from scripts.package_release import create_release
    folder = Path(tempfile.mkdtemp(prefix='ontology-release-'))
    try:
        archive = folder / 'release.zip'
        create_release(archive)
        return FileResponse(archive, media_type='application/zip', filename='Ontology-One-DEMO-本地演示平台.zip',
                            background=BackgroundTask(shutil.rmtree, folder, ignore_errors=True))
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def export_mysql_snapshot(target):
    """Write a stable, portable JSON snapshot of every application table."""
    with closing(connect()) as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema=DATABASE() AND table_type='BASE TABLE' ORDER BY table_name")]
        with target.open('w', encoding='utf-8') as stream:
            stream.write('{"format":"ontology-mysql-workspace-v1","database":"MySQL",')
            stream.write(json.dumps('created_at') + ':' + json.dumps(now()) + ',')
            stream.write(json.dumps('source_database') + ':' + json.dumps(mysql_config()['database']) + ',')
            stream.write('"tables":{')
            for index, table in enumerate(tables):
                if index:
                    stream.write(',')
                stream.write(json.dumps(table) + ':[')
                rows = conn.execute(f'SELECT * FROM `{table}`')
                first = True
                while True:
                    batch = rows.fetchmany(1000)
                    if not batch:
                        break
                    for row in batch:
                        if not first:
                            stream.write(',')
                        stream.write(json.dumps(dict(row), ensure_ascii=False, default=str))
                        first = False
                stream.write(']')
            stream.write('}}')


@router.get('/system/backup')
def backup():
    folder = Path(tempfile.mkdtemp(prefix='ontology-backup-'))
    try:
        snapshot = folder / 'mysql-workspace.json'
        export_mysql_snapshot(snapshot)
        archive = folder / 'workspace.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=5) as z:
            z.write(snapshot, 'data/mysql-workspace.json')
            for name in ('uploads', 'source'):
                base = ROOT / 'data' / name
                if base.exists():
                    for path in base.rglob('*'):
                        if path.is_file() and not path.is_symlink():
                            z.write(path, path.relative_to(ROOT).as_posix())
            z.writestr('backup-info.json', json.dumps({
                'platform': '集团本体平台', 'version': '0.1.0-demo', 'database': 'MySQL', 'created_at': now(),
                'contains': ['MySQL 全量业务表快照', '上传原件', '资料底座'],
                'excluded': ['AI接口密钥与运行配置'],
                'restore': '停止平台后，使用 scripts/restore_backup.py 将快照恢复到一个空的 MySQL 数据库，再恢复上传原件。'
            }, ensure_ascii=False, indent=2))
        return FileResponse(archive, media_type='application/zip', filename='Ontology-One-DEMO-工作区备份.zip',
                            background=BackgroundTask(shutil.rmtree, folder, ignore_errors=True))
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
