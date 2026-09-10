$ErrorActionPreference='Stop'
& $PSScriptRoot\build.ps1 -TestBuild
$compiler='C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$testExe=Join-Path $PSScriptRoot 'client-core-tests.exe'
$relayExe=Join-Path $PSScriptRoot '木林森中转2.0.1兼容版.exe'
& $compiler /nologo /target:exe /out:$testExe /reference:$relayExe (Join-Path $PSScriptRoot 'test_core.cs')
if($LASTEXITCODE-ne 0){exit $LASTEXITCODE}
& $testExe
exit $LASTEXITCODE
