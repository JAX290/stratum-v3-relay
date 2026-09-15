$ErrorActionPreference='Stop'
& $PSScriptRoot\build.ps1 -TestBuild
$compiler='C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$testExe=Join-Path $PSScriptRoot 'client-core-tests.exe'
$versionFile=Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) 'version.json'
$version=(Get-Content -LiteralPath $versionFile -Raw -Encoding UTF8 | ConvertFrom-Json).windows_client
$relayExe=Join-Path $PSScriptRoot ("木林森中转{0}测试版.exe" -f $version)
& $compiler /nologo /target:exe /out:$testExe /reference:$relayExe (Join-Path $PSScriptRoot 'test_core.cs')
if($LASTEXITCODE-ne 0){exit $LASTEXITCODE}
& $testExe
exit $LASTEXITCODE
