@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Ontology One 0.2.0 使用 Linux 容器运行。
echo 请先按 README 配置 .env 中的随机数据库密码。
docker compose up -d --build
if errorlevel 1 (pause & exit /b 1)
echo 首次运行请执行 docker compose exec api python scripts/bootstrap_admin.py --username group.admin
start http://127.0.0.1:8765
pause
