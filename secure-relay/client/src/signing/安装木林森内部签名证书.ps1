#Requires -RunAsAdministrator
param([switch]$Force)
$ErrorActionPreference = 'Stop'
$expectedThumbprint = 'D83C5C3E10822B6A4B45333EF41AFCC4F9156AAB'
$certificatePath = Join-Path $PSScriptRoot '木林森中转-内部代码签名证书.cer'
if (-not (Test-Path -LiteralPath $certificatePath)) { throw "没有找到公开证书：$certificatePath" }
$certificate = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($certificatePath)
if ($certificate.Thumbprint -ne $expectedThumbprint) { throw '证书指纹不正确，已停止安装。' }
if ($certificate.Subject -ne 'CN=木林森中转') { throw '证书发布者名称不正确，已停止安装。' }
Write-Warning '此操作会把“木林森中转”证书加入本机的“受信任的根证书颁发机构”和“受信任的发布者”，从而信任所有由该证书签名的程序。只应在自有矿场电脑上执行。'
if (-not $Force) {
  $answer = Read-Host '确认指纹无误后输入 INSTALL 继续'
  if ($answer -cne 'INSTALL') { throw '用户取消，系统证书库未修改。' }
}
Import-Certificate -FilePath $certificatePath -CertStoreLocation 'Cert:\LocalMachine\Root' | Out-Null
Import-Certificate -FilePath $certificatePath -CertStoreLocation 'Cert:\LocalMachine\TrustedPublisher' | Out-Null
Write-Host '木林森中转内部签名证书安装成功。' -ForegroundColor Green
Write-Host "证书指纹：$expectedThumbprint"
Write-Host '以后由该证书签名的木林森中转程序会显示为可信发布者。'
Read-Host '按回车键关闭'
