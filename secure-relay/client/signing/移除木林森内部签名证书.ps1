#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
$thumbprint = 'D83C5C3E10822B6A4B45333EF41AFCC4F9156AAB'
foreach ($store in @('Cert:\LocalMachine\Root','Cert:\LocalMachine\TrustedPublisher')) {
  $certificate = Get-ChildItem -LiteralPath $store | Where-Object { $_.Thumbprint -eq $thumbprint }
  if ($certificate) { $certificate | Remove-Item -Force }
}
Write-Host '木林森中转内部签名证书已从这台电脑移除。' -ForegroundColor Yellow
Read-Host '按回车键关闭'
