$ErrorActionPreference='Stop'
& $PSScriptRoot\build.ps1 -TestBuild
$compiler='C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$testExe=Join-Path $PSScriptRoot 'client-core-tests.exe'
$sourceText=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'StratumSecureRelay.cs') -Raw -Encoding UTF8
$versionMatch=[regex]::Match($sourceText,'AssemblyFileVersion\("(?<version>\d+\.\d+\.\d+)\.\d+"\)')
if(-not $versionMatch.Success){throw 'Could not read the client version from AssemblyFileVersion.'}
$relayExe=Join-Path $PSScriptRoot ("木林森中转{0}测试版.exe" -f $versionMatch.Groups['version'].Value)
& $compiler /nologo /target:exe /out:$testExe /reference:$relayExe (Join-Path $PSScriptRoot 'test_core.cs')
if($LASTEXITCODE-ne 0){exit $LASTEXITCODE}
& $testExe
exit $LASTEXITCODE
