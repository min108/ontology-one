"""Build a self-contained native Windows package on a Windows build machine.

Only CI/build time downloads are permitted. The delivered application never runs
pip/npm or downloads Python, MySQL or Harness. Release builds require every
artifact and redistributable DLL hash to have been reviewed in the lock files.
--bootstrap-lock is only for the initial QA branch: it emits a candidate lock,
marks the package non-release, and never publishes anything. It is not permitted
for tag builds. No provider key is required or read.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.5.0'
HOSTS = {'www.python.org', 'cdn.mysql.com', 'files.pythonhosted.org', 'download.visualstudio.microsoft.com', 'visualstudio.microsoft.com', 'codeload.github.com'}
VC_FILES = {'concrt140.dll', 'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
            'msvcp140_atomic_wait.dll', 'msvcp140_codecvt_ids.dll', 'vcruntime140.dll', 'vcruntime140_1.dll'}
EXCLUDE = {'.git', '.venv', 'node_modules', '__pycache__', 'work', 'data', 'uploads', 'runtime', '.pytest_cache'}


def build_environment():
    allowed = ('PATH','SYSTEMROOT','SystemRoot','WINDIR','COMSPEC','PATHEXT','TEMP','TMP','USERPROFILE',
               'APPDATA','LOCALAPPDATA','ProgramFiles','ProgramFiles(x86)','SSL_CERT_FILE','SSL_CERT_DIR',
               'HTTP_PROXY','HTTPS_PROXY','NO_PROXY','http_proxy','https_proxy','no_proxy')
    environment = {name: os.environ[name] for name in allowed if name in os.environ}
    environment['PIP_CONFIG_FILE'] = os.devnull
    return environment


def digest(path, algorithm='sha256'):
    with path.open('rb') as stream: return hashlib.file_digest(stream, algorithm).hexdigest()


class OfficialDownloads(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        approved_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def approved_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in HOSTS or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('Artifact download is not an approved official HTTPS origin')


def download(item, cache, *, bootstrap=False):
    approved_url(item['url'])
    if item.get('sha256') is not None and not re.fullmatch(r'[0-9a-f]{64}', item['sha256']):
        raise ValueError('Artifact SHA256 must contain exactly 64 lowercase hex characters')
    if item.get('upstream_md5') is not None and not re.fullmatch(r'[0-9a-f]{32}', item['upstream_md5']):
        raise ValueError('Vendor MD5 must contain exactly 32 lowercase hex characters')
    name = item['filename']
    if Path(name).name != name: raise ValueError('Invalid artifact filename')
    path = cache / name
    if not item.get('sha256') and not bootstrap:
        raise ValueError('Release dependency has no reviewed SHA256')
    if not path.exists():
        temporary = path.with_suffix(path.suffix+'.part')
        try:
            with urllib.request.build_opener(OfficialDownloads()).open(item['url'], timeout=120) as response, temporary.open('wb') as output:
                while chunk := response.read(1024*1024): output.write(chunk)
            temporary.replace(path)
        finally: temporary.unlink(missing_ok=True)
    observed = digest(path)
    if item.get('sha256') and observed != item['sha256']:
        raise ValueError('Downloaded dependency does not match its reviewed SHA256')
    if item.get('upstream_md5') and digest(path, 'md5') != item['upstream_md5']:
        raise ValueError('Downloaded dependency differs from the vendor publication checksum')
    item['sha256'] = observed
    return path


def safe_member(member):
    name = member.filename
    posix = PurePosixPath(name)
    if not name or '\\' in name or posix.is_absolute() or '..' in posix.parts or any(':' in part for part in posix.parts):
        raise ValueError('Unsafe dependency archive path')
    if stat.S_ISLNK(member.external_attr >> 16): raise ValueError('Dependency archive links are not accepted')
    return posix


def extract(archive, destination, *, mysql=False):
    with zipfile.ZipFile(archive) as source:
        for item in source.infolist():
            path = safe_member(item)
            if mysql:
                path = PurePosixPath(*path.parts[1:])
                name = path.as_posix().lower()
                keep = (name in {'bin/mysqld.exe', 'bin/mysql.exe', 'bin/mysqladmin.exe'} or
                        name.endswith('.dll') or name.startswith('share/') or
                        name.startswith('docs/') or path.name.upper().startswith(('LICENSE','COPYING','README')))
                if not keep or name.endswith(('.pdb','.lib')): continue
            if not path.parts or item.is_dir(): continue
            target = destination.joinpath(*path.parts);target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(item) as raw, target.open('wb') as output: shutil.copyfileobj(raw, output)


def source_files(package):
    names = ['README.md', 'requirements.txt', '.env.example', '启动平台.bat', '关闭平台.bat', '停止平台.bat']
    for name in names:
        if (ROOT/name).is_file(): shutil.copy2(ROOT/name, package/name)
    for directory in ('backend', 'ops', 'scripts', 'tests'):
        for path in (ROOT/directory).rglob('*'):
            if not path.is_file() or path.is_symlink(): continue
            relative = path.relative_to(ROOT)
            if any(part in EXCLUDE for part in relative.parts) or path.suffix in ('.pyc','.db','.log','.zip','.whl','.exe','.dll'):
                continue
            if path.name.startswith('.env') or path.name == 'source-credentials.json': continue
            target=package/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
    dist=ROOT/'frontend/dist'
    if not (dist/'index.html').is_file(): raise ValueError('Build frontend before packaging')
    shutil.copytree(dist, package/'frontend/dist')


def verify_signature(path):
    # Only approved filenames are passed, using a quoted environment variable.
    env = build_environment()
    env['ONTOLOGY_VERIFY_DLL']=str(path)
    command = "$s=Get-AuthenticodeSignature -LiteralPath $env:ONTOLOGY_VERIFY_DLL; if($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notmatch 'Microsoft Corporation'){exit 1}"
    completed=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],env=env,capture_output=True)
    if completed.returncode: raise ValueError('Redistributable DLL is not validly signed by Microsoft')


def vc_directory(lock, supplied):
    if supplied: return supplied.resolve()
    vswhere=Path(os.environ.get('ProgramFiles(x86)',r'C:\Program Files (x86)'))/'Microsoft Visual Studio/Installer/vswhere.exe'
    result=subprocess.run([str(vswhere),'-latest','-products','*','-property','installationPath'],text=True,capture_output=True,check=True)
    root=Path(result.stdout.strip())/'VC/Redist/MSVC'
    if lock.get('version'):
        return root/lock['version']/'x64/Microsoft.VC143.CRT'
    candidates=[path for path in root.glob('*/x64/Microsoft.VC143.CRT') if re.fullmatch(r'\d+\.\d+\.\d+(?:\.\d+)?',path.parents[1].name)]
    if not candidates: raise ValueError('Licensed Visual Studio app-local redistributables were not found')
    return max(candidates,key=lambda path:tuple(map(int,path.parents[1].name.split('.'))))


def install_vc(lock, package, harness_directory, supplied, bootstrap):
    directory=vc_directory(lock,supplied)
    if 'debug_nonredist' in str(directory).lower(): raise ValueError('Debug runtime redistribution is prohibited')
    version=directory.parents[1].name
    if lock.get('version') and lock['version']!=version: raise ValueError('VC runtime version differs from lock')
    if (not lock.get('version') or not lock.get('files')) and not bootstrap: raise ValueError('VC runtime lock is not reviewed')
    observed={}
    for name in sorted(VC_FILES):
        source=directory/name
        if not source.is_file(): raise ValueError('Required app-local VC runtime DLL is missing')
        verify_signature(source);value=digest(source)
        if lock.get('files') and lock['files'].get(name)!=value: raise ValueError('VC runtime DLL differs from lock')
        observed[name]=value
        for target in (package/'runtime/python',package/'runtime/skill-python',package/'runtime/mysql/bin',harness_directory):
            target.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target/name)
    lock.update(version=version,files=observed)


def build(args):
    if sys.platform != 'win32': raise ValueError('Native Windows packaging must run on Windows')
    if args.bootstrap_lock and os.getenv('GITHUB_REF','').startswith('refs/tags/'):
        raise ValueError('Tag/release builds cannot bootstrap dependency locks')
    commit = os.getenv('ONTOLOGY_BUILD_COMMIT')
    if commit:
        actual = subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,env=build_environment(),text=True,capture_output=True,check=True).stdout.strip()
        if not re.fullmatch(r'[0-9a-f]{40}', commit) or commit != actual:
            raise ValueError('Build provenance differs from checked-out source commit')
    runtime=copy.deepcopy(json.loads(args.runtime_lock.read_text(encoding='utf-8')))
    wheels=json.loads(args.wheels_lock.read_text(encoding='utf-8'))
    if wheels.get('requirements_sha256') != digest(ROOT/'requirements.txt'):
        raise ValueError('Windows wheel lock does not match requirements.txt')
    args.output.mkdir(parents=True,exist_ok=True);args.cache.mkdir(parents=True,exist_ok=True)
    package=args.output/f'Ontology-One-{VERSION}-windows-x64'
    if package.exists(): raise ValueError('Use a fresh package output directory')
    package.mkdir();source_files(package)
    def resolved():
        (args.output/'resolved-windows-runtime.lock.json').write_text(json.dumps(runtime,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    artifacts={}
    for name in ('python','mysql','mysql_source','vc_license'):
        artifacts[name]=download(runtime[name],args.cache,bootstrap=args.bootstrap_lock)
        resolved()
    python_archive,mysql_archive,mysql_source=(artifacts[name] for name in ('python','mysql','mysql_source'))
    extract(python_archive,package/'runtime/python');extract(python_archive,package/'runtime/skill-python');extract(mysql_archive,package/'runtime/mysql',mysql=True)
    wheel_paths=[download(item,args.cache) for item in wheels['wheels']]
    site=package/'runtime/python/Lib/site-packages'
    env=build_environment()
    subprocess.run([sys.executable,'-m','pip','install','--no-index','--no-deps','--no-compile','--target',str(site),*[str(path) for path in wheel_paths]],env=env,check=True)
    (package/'runtime/python/python312._pth').write_text('python312.zip\n.\nLib/site-packages\n../..\nimport site\n',encoding='utf-8')
    (package/'runtime/skill-python/python312._pth').write_text('python312.zip\n.\n',encoding='utf-8')
    harness=package/runtime['harness']['executable']
    if not harness.is_file() or digest(harness)!=runtime['harness']['sha256']: raise ValueError('Official Windows Harness executable digest differs')
    install_vc(runtime['vc_runtime'],package,harness.parent,args.vc_directory,args.bootstrap_lock)
    resolved()
    python=package/'runtime/python/python.exe'
    subprocess.run([str(python),'-I','-c','import fastapi,pymysql,cryptography,deepseek_harness,deepseek_harness_runtime,mcp,win32api,pywintypes,pythoncom,lxml.etree,yaml,pydantic_core,_cffi_backend; import sys; assert sys.version_info[:3]==(3,12,10)'],check=True,env=env)
    licenses=package/'runtime/licenses';licenses.mkdir()
    shutil.copy2(args.wheels_lock,licenses/'windows-wheels.lock.json')
    shutil.copy2(artifacts['vc_license'],licenses/runtime['vc_license']['filename'])
    notice=('THIRD-PARTY DISTRIBUTION NOTICES\n\nCPython: PSF license; complete license supplied in runtime/python/LICENSE.txt.\n'
        'Python wheels: retain every .dist-info license/notice; versions and origins in windows-wheels.lock.json.\n'
        'DeepSeek Harness: MIT and bundled THIRD_PARTY_NOTICES.md retained in its .dist-info/licenses directory.\n'
        'MySQL Community Server: GPL version 2 and bundled component notices retained under runtime/mysql.\n'
        f'Complete corresponding MySQL {runtime["mysql"]["version"]} source accompanies this release as {mysql_source.name};\n'
        'redistributors must supply it from the same distribution location alongside the binary package.\n'
        'Microsoft VC runtime: unmodified, Microsoft-signed app-local files from licensed Visual Studio 2022 VC/Redist.\n'
        'Redistribution list: '+runtime['vc_runtime']['license_url']+'\nLicense terms: '+runtime['vc_runtime']['terms_url']+'\n')
    (licenses/'NOTICE.txt').write_text(notice,encoding='utf-8')
    # The full corresponding source is a peer release asset, not an install dependency.
    shutil.copy2(mysql_source,args.output/mysql_source.name)
    manifest={'schema_version':1,'platform':'win_amd64','application_version':VERSION,'release_ready':not args.bootstrap_lock,
        'source_commit':commit or 'uncommitted-build',
        'skill_python':{'version':runtime['python']['version'],'executable':'runtime/skill-python/python.exe',
            'sha256':digest(package/'runtime/skill-python/python.exe'),'stdlib_sha256':digest(package/'runtime/skill-python/python312.zip')},
        'python':{'version':runtime['python']['version'],'executable':'runtime/python/python.exe','sha256':digest(python),'source_sha256':runtime['python']['sha256']},
        'mysql':{'version':runtime['mysql']['version'],'server':'runtime/mysql/bin/mysqld.exe','client':'runtime/mysql/bin/mysql.exe','admin':'runtime/mysql/bin/mysqladmin.exe',
            'server_sha256':digest(package/'runtime/mysql/bin/mysqld.exe'),
            'client_sha256':digest(package/'runtime/mysql/bin/mysql.exe'),
            'admin_sha256':digest(package/'runtime/mysql/bin/mysqladmin.exe'),'source_sha256':runtime['mysql']['sha256']},
        'harness':runtime['harness'],'vc_runtime':runtime['vc_runtime'],'wheels':wheels['wheels'],
        'upstream_sources':{name:runtime[name] for name in ('python','mysql','mysql_source')},
        'files':[{'path':path.relative_to(package).as_posix(),'sha256':digest(path),'bytes':path.stat().st_size}
                 for path in sorted((package/'runtime').rglob('*')) if path.is_file()]}
    (package/'runtime/manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (args.output/'resolved-windows-runtime.lock.json').write_text(json.dumps(runtime,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    output=args.output/f'ontology-one-{VERSION}-windows-x64.zip'
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for path in sorted(package.rglob('*')):
            if path.is_file():archive.write(path,path.relative_to(args.output).as_posix())
    assets=[output,args.output/mysql_source.name]
    if any(path.stat().st_size >= 2*1024**3 for path in assets):raise ValueError('A release asset exceeds GitHub individual asset limit')
    (args.output/'SHA256SUMS.txt').write_text(''.join(digest(path)+'  '+path.name+'\n' for path in assets),encoding='ascii')
    return {'package':str(output),'bytes':output.stat().st_size,'release_ready':manifest['release_ready'],'assets':[path.name for path in assets]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--cache',type=Path,required=True)
    parser.add_argument('--runtime-lock',type=Path,default=ROOT/'ops/windows-runtime.lock.json')
    parser.add_argument('--wheels-lock',type=Path,default=ROOT/'ops/windows-wheels.lock.json')
    parser.add_argument('--vc-directory',type=Path)
    parser.add_argument('--bootstrap-lock',action='store_true')
    args=parser.parse_args()
    print(json.dumps(build(args),ensure_ascii=False))


if __name__=='__main__':main()
