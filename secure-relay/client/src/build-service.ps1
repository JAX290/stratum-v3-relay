param([Parameter(Mandatory=$true)][string]$OutputDirectory,[switch]$TestBuild,[switch]$Sign,[string]$SigningThumbprint=$env:WINDOWS_SIGNING_THUMBPRINT,[string]$TimestampServer='http://timestamp.digicert.com')
$ErrorActionPreference='Stop'
# Development component only. Installation and signed service packaging are separate gates.
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$compiler='C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$sources=@('AppIdentity.cs','ConfigModels.cs','RelayManager.cs','RelayStatus.cs','FailoverPolicy.cs','RelayFailoverController.cs','RemoteControl.cs','NetworkHelper.cs','NetworkRecovery.cs',
 'MinerStatistics.cs','MinerHistoryStore.cs','RecoveryPolicy.cs','RecoverySession.cs',
 'WorkerProcess.cs','WatchdogSupervisor.cs','ServiceConfiguration.cs','ServiceStoragePermissions.cs','ServiceInstallerData.cs','RelayServiceHost.cs') |
 ForEach-Object {Join-Path $PSScriptRoot $_}
$versionFile=Join-Path (Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent) 'version.json'
$version=(Get-Content -LiteralPath $versionFile -Raw -Encoding UTF8|ConvertFrom-Json).windows_client
$assemblyInfo=Join-Path $OutputDirectory 'ServiceVersion.cs'
Set-Content -LiteralPath $assemblyInfo -Encoding UTF8 -Value ('[assembly: System.Reflection.AssemblyVersion("'+$version+'.0")]')
$output=Join-Path $OutputDirectory $(if($TestBuild){'MulinSenRelayService.test.exe'}else{'MulinSenRelayService.exe'})
& $compiler /nologo /target:exe /out:$output /reference:System.dll /reference:System.Core.dll `
 /reference:System.ServiceProcess.dll /reference:System.Runtime.Serialization.dll `
 /reference:System.Security.dll /reference:System.Web.Extensions.dll $sources $assemblyInfo
if($LASTEXITCODE-ne 0){throw 'Service component compilation failed.'}
if($Sign){
 $thumbprint=if($SigningThumbprint){$SigningThumbprint}else{'D83C5C3E10822B6A4B45333EF41AFCC4F9156AAB'}
 $certificate=Get-Item -LiteralPath ("Cert:\CurrentUser\My\{0}"-f$thumbprint) -ErrorAction Stop
 if(-not$certificate.HasPrivateKey){throw '木林森代码签名证书没有可用私钥。'}
 $signature=if($TimestampServer){Set-AuthenticodeSignature -LiteralPath $output -Certificate $certificate -HashAlgorithm SHA256 -TimestampServer $TimestampServer}
  else{Set-AuthenticodeSignature -LiteralPath $output -Certificate $certificate -HashAlgorithm SHA256}
 if($TestBuild){
  if(-not$signature.SignerCertificate-or$signature.SignerCertificate.Thumbprint-ne$certificate.Thumbprint){throw 'Test service signature does not match the selected certificate.'}
 } elseif($signature.Status-ne'Valid'){throw "Service signing failed: $($signature.Status)"}
}
Write-Host "Service development build: $output"
