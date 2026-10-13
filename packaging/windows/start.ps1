# 启动本机工作台，服务只监听 127.0.0.1。
param(
    [int]$Port = 8765,
    [switch]$NoBrowser
)
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$runtimePython = Join-Path $Root ".runtime\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $runtimePython -PathType Leaf)) {
    Write-Host "尚未安装运行环境，请先双击 install.cmd。" -ForegroundColor Red
    exit 1
}
$arguments = @((Join-Path $Root "main.py"), "serve", "--port", "$Port")
if (-not $NoBrowser) { $arguments += "--open" }
Write-Host "工作台地址：http://127.0.0.1:$Port （关闭此窗口即停止服务）"
& $runtimePython @arguments
exit $LASTEXITCODE
