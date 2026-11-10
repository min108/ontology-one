param([ValidateSet('start', 'stop')][string]$Action = 'start')
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root 'runtime\python\python.exe'
$Launcher = Join-Path $Root 'ops\windows_launcher.py'
try {
    if (-not [Environment]::Is64BitOperatingSystem) { throw '本运行包需要 Windows 10/11 64位操作系统，不支持32位系统。' }
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw '缺少包内运行时。请下载完整 Windows 运行包并解压后使用。' }
    Set-Location -LiteralPath $Root
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    & $Python '-I' '-B' $Launcher $Action
    $Result = $LASTEXITCODE
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    $Result = 1
}
if ($Result -ne 0) { Write-Host '操作未完成。请保留上方错误提示供排查。' -ForegroundColor Yellow }
Read-Host '按回车关闭此窗口'
exit $Result
