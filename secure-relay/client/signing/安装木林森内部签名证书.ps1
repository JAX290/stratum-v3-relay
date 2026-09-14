#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
$expectedThumbprint = 'D83C5C3E10822B6A4B45333EF41AFCC4F9156AAB'
$certificatePath = Join-Path $PSScriptRoot '木林森中转-内部代码签名证书.cer'
if (-not (Test-Path -LiteralPath $certificatePath)) { throw "没有找到公开证书：$certificatePath" }
$certificate = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($certificatePath)
if ($certificate.Thumbprint -ne $expectedThumbprint) { throw '证书指纹不正确，已停止安装。' }
if ($certificate.Subject -ne 'CN=木林森中转') { throw '证书发布者名称不正确，已停止安装。' }
Import-Certificate -FilePath $certificatePath -CertStoreLocation 'Cert:\LocalMachine\Root' | Out-Null
Import-Certificate -FilePath $certificatePath -CertStoreLocation 'Cert:\LocalMachine\TrustedPublisher' | Out-Null
Write-Host '木林森中转内部签名证书安装成功。' -ForegroundColor Green
Write-Host "证书指纹：$expectedThumbprint"
Write-Host '以后由该证书签名的木林森中转程序会显示为可信发布者。'
Read-Host '按回车键关闭'
