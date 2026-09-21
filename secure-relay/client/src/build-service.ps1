param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
# Development component only. Installation and signed service packaging are separate gates.
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$compiler='C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$sources=@('AppIdentity.cs','ConfigModels.cs','RelayManager.cs','RelayStatus.cs',
 'MinerStatistics.cs','MinerHistoryStore.cs','RecoveryPolicy.cs','RecoverySession.cs',
 'WorkerProcess.cs','WatchdogSupervisor.cs','ServiceConfiguration.cs','ServiceStoragePermissions.cs','RelayServiceHost.cs') |
 ForEach-Object {Join-Path $PSScriptRoot $_}
$versionFile=Join-Path (Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent) 'version.json'
$version=(Get-Content -LiteralPath $versionFile -Raw -Encoding UTF8|ConvertFrom-Json).windows_client
$assemblyInfo=Join-Path $OutputDirectory 'ServiceVersion.cs'
Set-Content -LiteralPath $assemblyInfo -Encoding UTF8 -Value ('[assembly: System.Reflection.AssemblyVersion("'+$version+'.0")]')
$output=Join-Path $OutputDirectory 'MulinSenRelayService.exe'
& $compiler /nologo /target:exe /out:$output /reference:System.dll /reference:System.Core.dll `
 /reference:System.ServiceProcess.dll /reference:System.Runtime.Serialization.dll `
 /reference:System.Security.dll /reference:System.Web.Extensions.dll $sources $assemblyInfo
if($LASTEXITCODE-ne 0){throw 'Service component compilation failed.'}
Write-Host "Service development build: $output"
