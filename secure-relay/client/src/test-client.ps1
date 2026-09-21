$ErrorActionPreference='Stop'
# Keep test builds separate from user-owned release and test executables.
$testDirectory=Join-Path ([IO.Path]::GetTempPath()) ('mulinsen-client-tests-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testDirectory | Out-Null
& $PSScriptRoot\build.ps1 -TestBuild -OutputDirectory $testDirectory
$compiler='C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$versionFile=Join-Path (Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent) 'version.json'
$version=(Get-Content -LiteralPath $versionFile -Raw -Encoding UTF8 | ConvertFrom-Json).windows_client
$relayExe=Join-Path $testDirectory ("木林森中转{0}测试版.exe" -f $version)
$testExe=Join-Path $testDirectory 'client-core-tests.exe'
& $compiler /nologo /target:exe /out:$testExe /reference:$relayExe (Join-Path $PSScriptRoot 'test_core.cs')
if($LASTEXITCODE-ne 0){throw 'Client regression tests failed to compile.'}
& $testExe
if($LASTEXITCODE-ne 0){throw 'Client regression tests failed.'}

# Compile the same core without WinForms/Drawing or any UI source files.
$coreSources=@('AppIdentity.cs','ConfigModels.cs','SystemStatus.cs','RelayManager.cs',
  'RelayStatus.cs','MinerStatistics.cs','MinerHistoryStore.cs','DutyStatus.cs','AdminAccessPolicy.cs','AccessPackage.cs','NetworkHelper.cs','NetworkRecovery.cs','ClientRepair.cs','LastKnownGoodConfiguration.cs','CompleteConfigurationValidator.cs','ConfigurationRollback.cs','FailoverPolicy.cs','RelayFailoverController.cs') |
  ForEach-Object { Join-Path $PSScriptRoot $_ }
$coreLibrary=Join-Path $testDirectory 'RelayCore.dll'
$coreAssemblyInfo=Join-Path $testDirectory 'CoreVersion.cs'
Set-Content -LiteralPath $coreAssemblyInfo -Encoding UTF8 -Value ('[assembly: System.Reflection.AssemblyVersion("'+$version+'.0")]')
& $compiler /nologo /target:library /out:$coreLibrary /reference:System.dll /reference:System.Core.dll `
  /reference:System.Runtime.Serialization.dll /reference:System.Security.dll /reference:System.Web.Extensions.dll $coreSources $coreAssemblyInfo
if($LASTEXITCODE-ne 0){throw 'Core must compile without Windows Forms.'}
$headlessTestExe=Join-Path $testDirectory 'headless-core-tests.exe'
& $compiler /nologo /target:exe /out:$headlessTestExe /reference:$coreLibrary (Join-Path $PSScriptRoot 'test_core.cs')
if($LASTEXITCODE-ne 0){throw 'Headless core tests failed to compile.'}
& $headlessTestExe
if($LASTEXITCODE-ne 0){throw 'Headless core tests failed.'}
Write-Host "Test artifacts: $testDirectory"

$failoverTestExe=Join-Path $testDirectory 'failover-policy-tests.exe'
& $compiler /nologo /target:exe /out:$failoverTestExe `
  (Join-Path $PSScriptRoot 'ConfigModels.cs') (Join-Path $PSScriptRoot 'FailoverPolicy.cs') `
  (Join-Path $PSScriptRoot 'RelayFailoverController.cs') (Join-Path $PSScriptRoot 'test_failover.cs') `
  /reference:System.Runtime.Serialization.dll /reference:System.Security.dll
if($LASTEXITCODE-ne 0){throw 'Failover policy tests failed to compile.'}
& $failoverTestExe
if($LASTEXITCODE-ne 0){throw 'Failover policy tests failed.'}

# Exercise the production TLS and relay path with an intentionally broken primary endpoint.
$testCertificate=New-SelfSignedCertificate -Subject 'CN=localhost' -DnsName 'localhost' `
  -CertStoreLocation 'Cert:\CurrentUser\My' -KeyAlgorithm RSA -KeyLength 2048 `
  -NotAfter (Get-Date).AddDays(1)
$pfxPath=Join-Path $testDirectory 'live-failover.pfx'
$pfxPassword=[guid]::NewGuid().ToString('N')
$securePassword=ConvertTo-SecureString -String $pfxPassword -AsPlainText -Force
try {
  Export-PfxCertificate -Cert $testCertificate -FilePath $pfxPath -Password $securePassword | Out-Null
  $liveFailoverExe=Join-Path $testDirectory 'live-failover-tests.exe'
  & $compiler /nologo /target:exe /out:$liveFailoverExe /reference:$coreLibrary `
    (Join-Path $PSScriptRoot 'test_live_failover.cs')
  if($LASTEXITCODE-ne 0){throw 'Live failover tests failed to compile.'}
& $liveFailoverExe $pfxPath $pfxPassword
  if($LASTEXITCODE-ne 0){throw 'Live failover tests failed.'}
} finally {
  Remove-Item -LiteralPath ("Cert:\CurrentUser\My\{0}" -f $testCertificate.Thumbprint) -Force -ErrorAction SilentlyContinue
}

$networkRecoveryTestExe=Join-Path $testDirectory 'network-recovery-tests.exe'
& $compiler /nologo /target:exe /out:$networkRecoveryTestExe /reference:System.dll `
  (Join-Path $PSScriptRoot 'NetworkRecovery.cs') (Join-Path $PSScriptRoot 'test_network_recovery.cs')
if($LASTEXITCODE-ne 0){throw 'Network recovery tests failed to compile.'}
& $networkRecoveryTestExe
if($LASTEXITCODE-ne 0){throw 'Network recovery tests failed.'}

$clientRepairTestExe=Join-Path $testDirectory 'client-repair-tests.exe'
& $compiler /nologo /target:exe /out:$clientRepairTestExe /reference:$coreLibrary `
  (Join-Path $PSScriptRoot 'test_client_repair.cs')
if($LASTEXITCODE-ne 0){throw 'Client repair tests failed to compile.'}
& $clientRepairTestExe
if($LASTEXITCODE-ne 0){throw 'Client repair tests failed.'}

$dutyStatusTestExe=Join-Path $testDirectory 'duty-status-tests.exe'
& $compiler /nologo /target:exe /out:$dutyStatusTestExe /reference:$coreLibrary (Join-Path $PSScriptRoot 'test_duty_status.cs')
if($LASTEXITCODE-ne 0){throw 'Duty status tests failed to compile.'}
& $dutyStatusTestExe
if($LASTEXITCODE-ne 0){throw 'Duty status tests failed.'}

$adminAccessTestExe=Join-Path $testDirectory 'admin-access-tests.exe'
& $compiler /nologo /target:exe /out:$adminAccessTestExe /reference:$coreLibrary (Join-Path $PSScriptRoot 'test_admin_access.cs')
if($LASTEXITCODE-ne 0){throw 'Admin access tests failed to compile.'}
& $adminAccessTestExe
if($LASTEXITCODE-ne 0){throw 'Admin access tests failed.'}

$accessPackageTestExe=Join-Path $testDirectory 'access-package-tests.exe'
& $compiler /nologo /target:exe /out:$accessPackageTestExe /reference:$coreLibrary (Join-Path $PSScriptRoot 'test_access_package.cs')
if($LASTEXITCODE-ne 0){throw 'Access package tests failed to compile.'}
& $accessPackageTestExe
if($LASTEXITCODE-ne 0){throw 'Access package tests failed.'}

# WIN-P01 first acceptance stage: recovery decisions, without installing services.
$recoveryTestExe=Join-Path $testDirectory 'recovery-policy-tests.exe'
& $compiler /nologo /target:exe /out:$recoveryTestExe /reference:System.Runtime.Serialization.dll `
  (Join-Path $PSScriptRoot 'RecoveryPolicy.cs') (Join-Path $PSScriptRoot 'test_recovery.cs')
if($LASTEXITCODE-ne 0){throw 'Recovery policy tests failed to compile.'}
& $recoveryTestExe
if($LASTEXITCODE-ne 0){throw 'Recovery policy tests failed.'}
$storeTestExe=Join-Path $testDirectory 'recovery-store-tests.exe'
& $compiler /nologo /target:exe /out:$storeTestExe /reference:System.Runtime.Serialization.dll `
  (Join-Path $PSScriptRoot 'RecoveryPolicy.cs') (Join-Path $PSScriptRoot 'RecoverySession.cs') `
  (Join-Path $PSScriptRoot 'test_recovery_store.cs')
if($LASTEXITCODE-ne 0){throw 'Recovery store tests failed to compile.'}
& $storeTestExe
if($LASTEXITCODE-ne 0){throw 'Recovery store tests failed.'}
& (Join-Path $PSScriptRoot 'build-service.ps1') -OutputDirectory $testDirectory
$serviceTestExe=Join-Path $testDirectory 'service-runtime-tests.exe'
& $compiler /nologo /target:exe /out:$serviceTestExe /reference:System.Runtime.Serialization.dll /reference:System.Security.dll `
  (Join-Path $PSScriptRoot 'ConfigModels.cs') (Join-Path $PSScriptRoot 'RecoveryPolicy.cs') `
  (Join-Path $PSScriptRoot 'RecoverySession.cs') (Join-Path $PSScriptRoot 'WorkerProcess.cs') `
  (Join-Path $PSScriptRoot 'WatchdogSupervisor.cs') (Join-Path $PSScriptRoot 'ServiceConfiguration.cs') `
  (Join-Path $PSScriptRoot 'ServiceStoragePermissions.cs') `
  (Join-Path $PSScriptRoot 'ServiceInstallerData.cs') `
  (Join-Path $PSScriptRoot 'test_service.cs')
if($LASTEXITCODE-ne 0){throw 'Service runtime tests failed to compile.'}
& $serviceTestExe
if($LASTEXITCODE-ne 0){throw 'Service runtime tests failed.'}
$planJson=& (Join-Path $PSScriptRoot 'install-service.ps1') -Mode Plan `
  -ServiceExecutable (Join-Path $testDirectory 'MulinSenRelayService.exe') `
  -DesktopConfig (Join-Path $testDirectory 'not-used.json') `
  -InstallDirectory (Join-Path $testDirectory 'planned-install') `
  -DataDirectory (Join-Path $testDirectory 'planned-data')
$plan=$planJson|ConvertFrom-Json
if($plan.Result -ne '只生成计划，未修改电脑' -or $plan.Steps.Count -lt 6){throw 'Service installation plan is incomplete.'}
if((Test-Path (Join-Path $testDirectory 'planned-install')) -or (Test-Path (Join-Path $testDirectory 'planned-data'))){throw 'Planning modified the computer.'}
Write-Host 'PASS service installation plan is complete and read-only'
