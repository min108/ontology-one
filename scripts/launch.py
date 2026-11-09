"""Start the local application and open its browser window."""
from pathlib import Path
import os,sys,subprocess,threading,time,webbrowser,urllib.request,json,socket,hashlib,shutil
ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT)
PORT=int(os.environ.get('ONTOLOGY_PORT','8765'))
url=f'http://127.0.0.1:{PORT}'
VERSION='0.1.0-demo'
WORKSPACE_ID=hashlib.sha256(str(ROOT.resolve()).encode()).hexdigest()[:16]
def open_browser():
    for _ in range(180):
        try:
            with urllib.request.urlopen(url+'/api/health',timeout=2) as r:
                if r.status==200:
                    webbrowser.open(url);return
        except Exception:time.sleep(1)
    print('启动时间较长，请查看上方日志。')
if not (ROOT/'frontend/dist/index.html').exists():
    print('未找到编译后的界面，请先在 frontend 执行 npm install 和 npm run build。');sys.exit(1)
try:
    with urllib.request.urlopen(url+'/api/health',timeout=2) as response:
        running=json.load(response)
        if running.get('service')=='集团本体平台':
            if running.get('version')!=VERSION or running.get('workspace_id')!=WORKSPACE_ID:
                print('另一个版本或目录中的Ontology One仍在运行。',flush=True)
                print('请先切回原来的启动窗口，按 Ctrl+C 停止旧服务，再双击本目录的启动平台.bat。',flush=True)
                print('本次没有打开旧页面，也没有改动任何工作区数据。',flush=True)
                sys.exit(1)
            print('本地平台已经运行，正在打开工作区。',flush=True)
            webbrowser.open(url)
            sys.exit(0)
except (OSError,ValueError):
    pass
with socket.socket() as probe:
    try:probe.bind(('127.0.0.1',PORT))
    except OSError:
        print(f'端口 {PORT} 已被其他程序占用。请关闭占用程序，或设置 ONTOLOGY_PORT 为其他端口后重新启动。')
        sys.exit(1)
def prepare_mysql():
    if os.environ.get('ONTOLOGY_MYSQL_URL') or os.environ.get('MYSQL_HOST'):
        print('使用环境变量指定的 MySQL 数据服务。',flush=True)
        return
    if not shutil.which('docker'):
        print('未检测到 Docker。请先启动本机 MySQL 8，并设置 MYSQL_HOST、MYSQL_PORT、MYSQL_DATABASE、MYSQL_USER、MYSQL_PASSWORD；或安装 Docker 后重新启动。',flush=True)
        sys.exit(1)
    print('正在准备本机 MySQL 8 数据服务（首次需要下载镜像）…',flush=True)
    try:
        subprocess.run(['docker','compose','up','-d','--wait'],cwd=ROOT,check=True)
    except (OSError,subprocess.CalledProcessError):
        print('MySQL 容器未能启动。请确认 Docker 已运行，或改用环境变量连接已有 MySQL 8。',flush=True)
        sys.exit(1)
    os.environ.setdefault('MYSQL_HOST','127.0.0.1')
    os.environ.setdefault('MYSQL_PORT','3306')
    os.environ.setdefault('MYSQL_DATABASE','ontology_one')
    os.environ.setdefault('MYSQL_USER','ontology')
    os.environ.setdefault('MYSQL_PASSWORD','ontology_local')
prepare_mysql()
print('Ontology One DEMO · 本地启动中。首次启动生成少量虚构通用示例。',flush=True)
print('本地访问地址：'+url,flush=True)
threading.Thread(target=open_browser,daemon=True).start()
try:subprocess.run([sys.executable,'-m','uvicorn','backend.main:app','--host','127.0.0.1','--port',str(PORT)],check=True)
except KeyboardInterrupt:pass
