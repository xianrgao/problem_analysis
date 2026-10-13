# 普通用户权限安装：校验离线包、准备 Python 3.13、创建项目独立环境并安装锁定依赖。
# 不修改 PATH，不覆盖已有 Python 环境，不访问公网。
param(
    [string]$Python = "",
    [switch]$SkipVerify
)
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location -LiteralPath $Root

function Fail($message) {
    Write-Host $message -ForegroundColor Red
    exit 1
}

function Test-Python313($exe) {
    if (-not $exe -or -not (Test-Path -LiteralPath $exe -PathType Leaf)) { return $false }
    try {
        $version = & $exe -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        return ($LASTEXITCODE -eq 0 -and "$version".Trim() -eq "3.13")
    } catch {
        return $false
    }
}

if (-not $SkipVerify) {
    & (Join-Path $Root "verify.ps1")
    if ($LASTEXITCODE -ne 0) { Fail "安装已停止：离线包不完整。" }
}

# 1. Python 3.13：显式参数 → 本包已安装副本 → 用户级标准位置 → py 启动器 → 离线安装包。
$bootstrap = Join-Path $Root ".bootstrap\python313"
$candidates = @($Python, (Join-Path $bootstrap "python.exe"))
if ($env:LOCALAPPDATA) { $candidates += (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe") }
$selected = $candidates | Where-Object { Test-Python313 $_ } | Select-Object -First 1
if (-not $selected -and (Get-Command py -ErrorAction SilentlyContinue)) {
    try {
        $found = & py -3.13 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and (Test-Python313 "$found".Trim())) { $selected = "$found".Trim() }
    } catch { }
}
if (-not $selected) {
    $installer = Get-ChildItem -LiteralPath (Join-Path $Root "python") -Filter "python-3.13.*-amd64.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $installer) { Fail "未找到 Python 3.13，离线包中也没有 python\python-3.13.x-amd64.exe。请使用 -Python 指定 python.exe。" }
    Write-Host "正在以当前用户身份安装 Python 3.13 到本目录 .bootstrap（不修改 PATH）……"
    $arguments = @("/quiet", "InstallAllUsers=0", "TargetDir=`"$bootstrap`"", "PrependPath=0", "Include_launcher=0",
                   "InstallLauncherAllUsers=0", "AssociateFiles=0", "Shortcuts=0", "Include_test=0", "Include_doc=0")
    $process = Start-Process -FilePath $installer.FullName -ArgumentList $arguments -Wait -PassThru
    if ($process.ExitCode -ne 0) { Fail "Python 安装程序返回 $($process.ExitCode)，请查看 %TEMP% 下的 Python 安装日志。" }
    $selected = Join-Path $bootstrap "python.exe"
    if (-not (Test-Python313 $selected)) { Fail "Python 安装后仍不可用：$selected" }
}
Write-Host "使用 Python：$selected"

# 2. 项目独立环境 .runtime，只从本包 wheels 安装锁定依赖。
$runtime = Join-Path $Root ".runtime"
$runtimePython = Join-Path $runtime "Scripts\python.exe"
if (-not (Test-Python313 $runtimePython)) {
    & $selected -m venv $runtime
    if ($LASTEXITCODE -ne 0) { Fail "创建项目环境失败。" }
}
& $runtimePython -m pip install --disable-pip-version-check --no-index --find-links (Join-Path $Root "wheels") -r (Join-Path $Root "requirements.lock")
if ($LASTEXITCODE -ne 0) { Fail "依赖安装失败：wheels 目录与 requirements.lock 不一致。" }

# 3. 校验或生成模拟数据。
& $runtimePython (Join-Path $Root "main.py") init-demo
if ($LASTEXITCODE -ne 0) { Fail "模拟数据准备失败。" }
Write-Host "安装完成。双击 start.cmd 启动工作台（http://127.0.0.1:8765）。" -ForegroundColor Green
exit 0
