"""Create a portable, fresh-workspace 0.5.0 release without credentials or personal data."""
from pathlib import Path
import argparse
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {'node_modules', '.git', '.venv', '__pycache__', 'work', 'runtime', 'uploads'}
TOP_FILES = ['README.md', 'requirements.txt', 'docker-compose.yml', '启动平台.bat', 'start.sh', '.gitignore', 'Dockerfile', '.dockerignore', '.env.example']


def create_release(destination: Path):
    if not (ROOT / 'frontend/dist/index.html').is_file():
        raise RuntimeError('请先编译前端页面，再导出本地安装包。')
    destination.parent.mkdir(parents=True, exist_ok=True)
    files = [ROOT / name for name in TOP_FILES if (ROOT / name).is_file()]
    for folder in ['backend', 'frontend', 'scripts', 'ops', 'tests']:
        for path in (ROOT / folder).rglob('*'):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(ROOT)
            if any(part in EXCLUDED for part in relative.parts):
                continue
            if path.name.startswith('.env') or path.name == 'source-credentials.json' or path.suffix in {'.pyc', '.tsbuildinfo', '.db', '.log'}:
                continue
            files.append(path)
    prefix = 'Ontology-One-0.5.0/'
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(set(files)):
            archive.write(path, prefix + path.relative_to(ROOT).as_posix())
    return {'path': str(destination), 'file_count': len(set(files)), 'bytes': destination.stat().st_size}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    print(json.dumps(create_release(parser.parse_args().output), ensure_ascii=False))
