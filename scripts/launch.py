"""Launch a locally configured Linux/macOS development environment."""
from pathlib import Path
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0,str(ROOT))
from backend.version import APP_VERSION


def main():
    if os.name == 'nt':
        raise SystemExit('Windows请使用完整原生运行包，并双击“启动平台.bat”。')
    if not os.environ.get('MYSQL_HOST') and not os.environ.get('ONTOLOGY_MYSQL_URL'):
        raise SystemExit('请配置已迁移的MYSQL_*环境变量；完整容器启动请按README执行docker compose。')
    if not (ROOT / 'frontend/dist/index.html').is_file():
        raise SystemExit('请先运行 npm --prefix frontend ci 和 npm --prefix frontend run build。')
    port = int(os.getenv('ONTOLOGY_PORT', '8765'))
    url = f'http://127.0.0.1:{port}'
    def open_browser():
        for _ in range(60):
            try:
                with urllib.request.urlopen(url + '/api/health', timeout=2) as response:
                    if response.status == 200:
                        webbrowser.open(url)
                        return
            except OSError:
                time.sleep(1)
    print(f'Ontology One {APP_VERSION} · 集团本体平台：' + url, flush=True)
    threading.Thread(target=open_browser, daemon=True).start()
    subprocess.run([sys.executable, '-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', str(port), '--workers', os.getenv('ONTOLOGY_API_WORKERS', '4')], check=True)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
