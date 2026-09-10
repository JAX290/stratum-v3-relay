param([switch]$TestBuild)
$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot 'StratumSecureRelay.cs'
$output = Join-Path $PSScriptRoot $(if ($TestBuild) { '木林森中转2.1.4测试版.exe' } else { '木林森中转.exe' })
$icon = Join-Path $PSScriptRoot '木林森.ico'
$compiler = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw "C# compiler not found: $compiler" }
if (-not (Test-Path -LiteralPath $icon)) { throw "Application icon not found: $icon" }
& $compiler /nologo /target:winexe /optimize+ /platform:anycpu /win32icon:$icon /out:$output `
  /reference:System.dll /reference:System.Core.dll /reference:System.Drawing.dll `
  /reference:System.Windows.Forms.dll /reference:System.Runtime.Serialization.dll `
  /reference:System.Security.dll /reference:System.Web.Extensions.dll $source
if ($LASTEXITCODE -ne 0) { throw "Build failed with exit code $LASTEXITCODE" }
Get-FileHash -Algorithm SHA256 -LiteralPath $output
