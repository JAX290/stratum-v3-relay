param([switch]$TestBuild)
$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot 'StratumSecureRelay.cs'
$sourceText = Get-Content -LiteralPath $source -Raw -Encoding UTF8
$versionMatch = [regex]::Match($sourceText, 'AssemblyFileVersion\("(?<version>\d+\.\d+\.\d+)\.\d+"\)')
if (-not $versionMatch.Success) { throw 'Could not read the client version from AssemblyFileVersion.' }
$version = $versionMatch.Groups['version'].Value
$edition = if ($TestBuild) { '测试版' } else { '正式版' }
$output = Join-Path $PSScriptRoot ("木林森中转{0}{1}.exe" -f $version, $edition)
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
