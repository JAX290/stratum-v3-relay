# 木林森中转移守电脑初始化脚本主体
#
# 本文件是"单文件版"的脚本主体，由 build-setup.ps1 拼进 watch-pc-setup-<版本>.bat。
# 不要直接双击本文件（.ps1 双击会用记事本打开），请使用生成出来的 .bat。
#
# 只有第一次需要在值守电脑上运行：它把客户端目录加入 Defender 排除，然后下载并启动
# 当前发布版本。之后的自动升级不会再被拦截。

$ErrorActionPreference = 'Stop'
$InstallDirectory = 'C:\MulinSenRelay'
$ConfigDirectory  = Join-Path $env:LOCALAPPDATA 'StratumSecureRelay'
$GateBase         = 'https://status1.mulinsen.win/exe'
$ThreatName       = 'Trojan:Script/Conteban.A!ml'

function Write-Step([string]$Text) { Write-Host "==> $Text" -ForegroundColor Cyan }
function Write-Ok([string]$Text)   { Write-Host "    完成：$Text" -ForegroundColor Green }
function Write-Warn([string]$Text) { Write-Host "    注意：$Text" -ForegroundColor Yellow }

Write-Host ''
Write-Host '  木林森中转移守电脑初始化' -ForegroundColor White
Write-Host '  ----------------------------------------' -ForegroundColor DarkGray

# 1) 排除目录：必须在下载和运行之前完成，否则下载到就被删除
Write-Step '把客户端目录加入 Defender 排除'
try {
    New-Item -ItemType Directory -Force -Path $InstallDirectory | Out-Null
    Add-MpPreference -ExclusionPath $InstallDirectory -ErrorAction Stop
    Write-Ok $InstallDirectory
} catch {
    Write-Warn "无法写入 Defender 排除（$($_.Exception.Message)）"
    Write-Warn '如果这台机器装有第三方杀毒软件，请手动把该目录加入它的白名单。'
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
        Write-Ok '已请求恢复（若从未被隔离，此处没有实际动作）'
    } catch {
        Write-Warn "恢复请求失败：$($_.Exception.Message)"
    }
} else {
    Write-Warn '找不到 MpCmdRun.exe，跳过恢复步骤'
}

# 3) 读取发布清单并下载
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
    throw '无法读取发布清单，请检查网络是否可用。'
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
            throw '下载闸门当前未开放。请让管理员在管理面板「设置 → 版本与更新 → 客户端下载闸门」开放后再运行本工具。'
        }
        if ($attempt -eq 2) { throw }
        Start-Sleep -Seconds 2
    }
}

# 4) 校验
Write-Step '校验安装包'
$actual = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -ne $manifest.sha256.ToLowerInvariant()) {
    Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
    throw "摘要不一致，已删除下载的文件。期望 $($manifest.sha256)，实际 $actual"
}
Write-Ok "SHA-256 一致（$($actual.Substring(0,16))…）"

# 5) 启动
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
Write-Host '  以后升级不需要再运行本工具。' -ForegroundColor Gray
Write-Host ''
Read-Host '按回车关闭'
