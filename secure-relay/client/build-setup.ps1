# 把脚本主体拼成"单文件双击运行"的混合批处理。
#
# 生成的 .bat 前半段是纯 ASCII 批处理：检查管理员权限，必要时用 UAC 重新启动自己，
# 然后用 PowerShell 读取自身、截取标记之后的部分并执行。后半段是 UTF-8 的 PowerShell。
#
# 两个编码要求（中文 Windows 上的老问题）：
#   * 批处理部分必须是纯 ASCII —— 否则控制台代码页不同时会被解析坏
#   * PowerShell 部分由 ReadAllText(..., UTF8) 显式按 UTF-8 读取，不受代码页影响
# 因此整个文件保存为 UTF-8 **不带 BOM**（BOM 会让 cmd 解析首行出错），行尾用 CRLF。

param(
    [string]$BodyPath,
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'

$header = @'
@echo off
setlocal
set "SELF=%~f0"
rem Administrator rights are required: Defender exclusions and writing C:\MulinSenRelay
net session >nul 2>&1
if errorlevel 1 (
  echo.
  echo   Requesting administrator rights... please click Yes in the dialog.
  echo.
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath $env:SELF -Verb RunAs"
  exit /b 0
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$t=[IO.File]::ReadAllText($env:SELF,[Text.Encoding]::UTF8); $m='##'+'PS##'; $i=$t.IndexOf($m); if($i -lt 0){exit 1}; iex $t.Substring($i+$m.Length)"
exit /b
##PS##
'@

$body = [System.IO.File]::ReadAllText($BodyPath, [System.Text.Encoding]::UTF8)
if ($body.Contains('##PS##')) { throw '脚本主体里不能出现 ##PS## 标记。' }

# 头部用 CRLF 并保证是纯 ASCII（中文只允许出现在正文里，由 UTF-8 读取）
$headerLines = $header -split "`r?`n"
foreach ($line in $headerLines) {
    if ($line -match '[^\x00-\x7F]') {
        throw "批处理头部含有非 ASCII 字符，会导致解析错误：$line"
    }
}
$combined = ($header.TrimEnd() + "`r`n" + $body.TrimEnd() + "`r`n")
$combined = $combined -replace "`r`n", "`n" -replace "`n", "`r`n"

[System.IO.File]::WriteAllText($OutputPath, $combined, (New-Object System.Text.UTF8Encoding($false)))

$bytes = [System.IO.File]::ReadAllBytes($OutputPath)
Write-Host "  生成: $OutputPath"
Write-Host "  大小: $([math]::Round($bytes.Length / 1KB, 1)) KB"
Write-Host "  BOM:  $($bytes[0] -eq 239)（必须为 False）"
Write-Host "  CRLF: $((($bytes | Where-Object { $_ -eq 13 }).Count) -gt 0)"
