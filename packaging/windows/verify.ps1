# 校验离线目录中的文件与 SHA256SUMS.txt 一致。
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Sums = Join-Path $Root "SHA256SUMS.txt"
if (-not (Test-Path -LiteralPath $Sums)) {
    Write-Host "缺少 SHA256SUMS.txt，无法校验离线包。" -ForegroundColor Red
    exit 1
}
$failed = 0
$count = 0
foreach ($line in [System.IO.File]::ReadAllLines($Sums, [System.Text.Encoding]::UTF8)) {
    if (-not $line.Trim()) { continue }
    $parts = $line -split "  ", 2
    $expected = $parts[0]
    $relative = $parts[1]
    $path = Join-Path $Root $relative
    $count++
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        Write-Host "缺少文件：$relative" -ForegroundColor Red
        $failed++
        continue
    }
    $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) {
        Write-Host "校验值不一致：$relative" -ForegroundColor Red
        $failed++
    }
}
if ($failed -gt 0) {
    Write-Host "离线包校验失败：$failed / $count 个文件异常，请重新复制离线包。" -ForegroundColor Red
    exit 1
}
Write-Host "离线包校验通过：$count 个文件。"
exit 0
