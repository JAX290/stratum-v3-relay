param([switch]$TestBuild,[switch]$Sign,[string]$SigningThumbprint=$env:WINDOWS_SIGNING_THUMBPRINT,[string]$OutputDirectory=(Split-Path $PSScriptRoot -Parent))
$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot 'StratumSecureRelay.cs'
$configSource = Join-Path $PSScriptRoot 'ConfigModels.cs'
$moduleSources = @('AppIdentity.cs','AppBrand.cs','SystemStatus.cs','CrashRecovery.cs',
  'RelayManager.cs','RelayStatus.cs','MinerStatistics.cs','MinerHistoryStore.cs','NetworkHelper.cs','NetworkRecovery.cs','ClientRepair.cs','LastKnownGoodConfiguration.cs','CompleteConfigurationValidator.cs','ConfigurationRollback.cs','FailoverPolicy.cs','RelayFailoverController.cs',
  'MainForm.cs','DiagnosticReportForm.cs','MinerStatusForm.cs','BackupForm.cs') | ForEach-Object { Join-Path $PSScriptRoot $_ }
$versionFile = Join-Path (Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent) 'version.json'
if (-not (Test-Path -LiteralPath $versionFile)) { throw "Version file not found: $versionFile" }
$version = (Get-Content -LiteralPath $versionFile -Raw -Encoding UTF8 | ConvertFrom-Json).windows_client
if ($version -notmatch '^\d+\.\d+\.\d+$') { throw 'windows_client version must use x.y.z format.' }
$assemblyInfo = Join-Path ([IO.Path]::GetTempPath()) ("StratumVersion-{0}.cs" -f [guid]::NewGuid().ToString('N'))
Set-Content -LiteralPath $assemblyInfo -Encoding UTF8 -Value @"
using System.Reflection;
[assembly: AssemblyVersion("$version.0")]
[assembly: AssemblyFileVersion("$version.0")]
"@
$edition = if ($TestBuild) { '测试版' } else { '正式版' }
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$output = Join-Path $OutputDirectory ("木林森中转{0}{1}.exe" -f $version, $edition)
$icon = Join-Path $PSScriptRoot '木林森.ico'
$compiler = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compiler)) { throw "C# compiler not found: $compiler" }
if (-not (Test-Path -LiteralPath $icon)) { throw "Application icon not found: $icon" }
try {
  & $compiler /nologo /target:winexe /optimize+ /platform:anycpu /win32icon:$icon /out:$output `
    /reference:System.dll /reference:System.Core.dll /reference:System.Drawing.dll `
    /reference:System.Windows.Forms.dll /reference:System.Runtime.Serialization.dll `
    /reference:System.Security.dll /reference:System.Web.Extensions.dll $source $configSource $moduleSources $assemblyInfo
  if ($LASTEXITCODE -ne 0) { throw "Build failed with exit code $LASTEXITCODE" }
} finally {
  Remove-Item -LiteralPath $assemblyInfo -Force -ErrorAction SilentlyContinue
}
if ($Sign) {
  $thumbprint = if ($SigningThumbprint) { $SigningThumbprint } else { 'D83C5C3E10822B6A4B45333EF41AFCC4F9156AAB' }
  $certificate = Get-Item -LiteralPath ("Cert:\CurrentUser\My\{0}" -f $thumbprint) -ErrorAction Stop
  if (-not $certificate.HasPrivateKey) { throw '木林森代码签名证书没有可用私钥。' }
  $signature = Set-AuthenticodeSignature -LiteralPath $output -Certificate $certificate -HashAlgorithm SHA256 -TimestampServer 'http://timestamp.digicert.com'
  if ($signature.Status -ne 'Valid') { throw "Code signing failed: $($signature.Status) $($signature.StatusMessage)" }
  Write-Host "Signed by $($certificate.Subject); thumbprint $thumbprint"
}
Get-FileHash -Algorithm SHA256 -LiteralPath $output
