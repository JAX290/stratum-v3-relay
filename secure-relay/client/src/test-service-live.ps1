param(
    [Parameter(Mandatory=$true)][string]$ServiceExecutable,
    [Parameter(Mandatory=$true)][string]$ExpectedThumbprint
)
$ErrorActionPreference='Stop'
$serviceName='MulinSenRelaySupervisorTest'
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$principal=[Security.Principal.WindowsPrincipal]$identity
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw '真实服务验收必须以管理员身份运行。'
}
$file=Get-Item -LiteralPath $ServiceExecutable -ErrorAction Stop
if($file.Name -ne 'MulinSenRelayService.test.exe') {throw '只允许安装明确命名的测试服务程序。'}
$signature=Get-AuthenticodeSignature -LiteralPath $file.FullName
if(-not $signature.SignerCertificate -or $signature.SignerCertificate.Subject -ne 'CN=木林森中转' -or
    $signature.SignerCertificate.Thumbprint -ne $ExpectedThumbprint) {
    throw '测试服务签名无效。'
}
$testId=[guid]::NewGuid().ToString('N')
$dataDirectory=[IO.Path]::GetFullPath((Join-Path $env:ProgramData ('MulinSenRelayServiceTest-'+$testId)))
$installDirectory=[IO.Path]::GetFullPath((Join-Path $env:ProgramData ('MulinSenRelayServiceTestBin-'+$testId)))
$safeDataPrefix=[IO.Path]::GetFullPath((Join-Path $env:ProgramData 'MulinSenRelayServiceTest-'))
$safeInstallPrefix=[IO.Path]::GetFullPath((Join-Path $env:ProgramData 'MulinSenRelayServiceTestBin-'))
if(-not $dataDirectory.StartsWith($safeDataPrefix,[StringComparison]::OrdinalIgnoreCase) -or
   -not $installDirectory.StartsWith($safeInstallPrefix,[StringComparison]::OrdinalIgnoreCase)) {
    throw '隔离测试目录越界。'
}
$desktopConfig=Join-Path $env:TEMP ('mulinsen-service-config-'+$testId+'.json')
$listener=New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback,0)
$listener.Start()
$port=([Net.IPEndPoint]$listener.LocalEndpoint).Port
$listener.Stop()
$installer=Join-Path $PSScriptRoot 'install-service.ps1'

function Get-TestService {
    Get-CimInstance Win32_Service -Filter "Name='$serviceName'" -ErrorAction SilentlyContinue
}
function Wait-State([string]$State,[int]$Seconds) {
    $until=(Get-Date).AddSeconds($Seconds)
    do {
        $item=Get-TestService
        if($item -and $item.State -eq $State) {return $item}
        Start-Sleep -Milliseconds 500
    } while((Get-Date) -lt $until)
    throw "测试服务未进入 $State 状态。"
}
function Get-Hash([string]$Path) {
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

$created=$false
try {
    if(Get-TestService) {throw '测试服务名称已存在，请先检查上一次验收。'}
    & $file.FullName --create-test-config $desktopConfig $port
    if($LASTEXITCODE -ne 0) {throw '无法创建隔离测试配置。'}
    $installOutput=@(& $installer -Mode Install -ServiceExecutable $file.FullName -DesktopConfig $desktopConfig `
        -InstallDirectory $installDirectory -DataDirectory $dataDirectory -IsolatedTest `
        -ExpectedTestThumbprint $ExpectedThumbprint)
    $created=$true
    $file=Get-Item -LiteralPath (Join-Path $installDirectory 'MulinSenRelayService.test.exe')
    $configPath=Join-Path $dataDirectory 'service-config.dat'
    $recoveryPath=Join-Path $dataDirectory 'recovery.json'
    if(-not(Test-Path -LiteralPath $configPath) -or -not(Test-Path -LiteralPath $recoveryPath)) {
        $entries=@(Get-ChildItem -LiteralPath $dataDirectory -Force | Select-Object -ExpandProperty Name)
        throw "安装脚本返回后缺少服务配置。输出：$($installOutput -join ' ')；目录：$($entries -join ',')"
    }
    $before=@{Executable=Get-Hash $file.FullName;Config=Get-Hash $configPath;Recovery=Get-Hash $recoveryPath}
    Set-Content -LiteralPath $desktopConfig -Value '{invalid' -Encoding ASCII
    $rollbackObserved=$false
    try {
        & $installer -Mode Install -ServiceExecutable $ServiceExecutable -DesktopConfig $desktopConfig `
            -InstallDirectory $installDirectory -DataDirectory $dataDirectory -IsolatedTest `
            -ExpectedTestThumbprint $ExpectedThumbprint | Out-Null
    } catch {$rollbackObserved=$true}
    if(-not(Test-Path -LiteralPath $file.FullName) -or -not(Test-Path -LiteralPath $configPath) -or
       -not(Test-Path -LiteralPath $recoveryPath)) {throw '安装失败回退删除了原有服务文件或配置。'}
    if(-not $rollbackObserved -or (Get-Hash $file.FullName) -ne $before.Executable -or
       (Get-Hash $configPath) -ne $before.Config -or (Get-Hash $recoveryPath) -ne $before.Recovery) {
        throw '安装失败后未完整恢复原服务文件和配置。'
    }
    $registration=Get-TestService
    if(-not $registration -or $registration.StartMode -ne 'Manual' -or
       $registration.StartName -ne 'NT AUTHORITY\LocalService') {
        throw '测试服务准备阶段的启动模式或账户不正确。'
    }
    & $env:SystemRoot\System32\sc.exe config $serviceName 'start=' 'auto' | Out-Null
    if($LASTEXITCODE -ne 0) {throw '无法配置测试服务自动启动。'}
    $registration=Get-TestService
    if($registration.StartMode -ne 'Auto') {throw '测试服务自动启动配置未生效。'}
    & $env:SystemRoot\System32\sc.exe start $serviceName | Out-Null
    if($LASTEXITCODE -ne 0) {throw '测试服务启动失败。'}
    $service=Wait-State Running 30
    $statusPath=Join-Path $dataDirectory 'service-status.txt'
    $until=(Get-Date).AddSeconds(90)
    do {
        if((Test-Path -LiteralPath $statusPath) -and ((Get-Content -LiteralPath $statusPath -Raw) -match 'Healthy')) {break}
        Start-Sleep 1
    } while((Get-Date) -lt $until)
    if(-not (Test-Path -LiteralPath $statusPath) -or (Get-Content -LiteralPath $statusPath -Raw) -notmatch 'Healthy') {
        $status=$(if(Test-Path -LiteralPath $statusPath){Get-Content -LiteralPath $statusPath -Raw}else{'无状态文件'})
        $serviceState=Get-TestService
        throw "LocalService 未通过本地健康检查。服务状态：$($serviceState.State)；记录：$status"
    }
    $worker=Get-CimInstance Win32_Process -Filter "ParentProcessId=$($service.ProcessId)" |
        Where-Object {$_.Name -eq $file.Name} | Select-Object -First 1
    if(-not $worker) {throw '未找到独立中转工作进程。'}
    Stop-Process -Id $worker.ProcessId -Force
    $until=(Get-Date).AddSeconds(60)
    $replacement=$null
    do {
        $service=Get-TestService
        $replacement=Get-CimInstance Win32_Process -Filter "ParentProcessId=$($service.ProcessId)" |
            Where-Object {$_.Name -eq $file.Name -and $_.ProcessId -ne $worker.ProcessId} | Select-Object -First 1
        if($replacement) {break}
        Start-Sleep 1
    } while((Get-Date) -lt $until)
    if(-not $replacement) {throw '工作进程退出后未能按策略恢复。'}
    & $env:SystemRoot\System32\sc.exe stop $serviceName | Out-Null
    Wait-State Stopped 30 | Out-Null
    if((Get-Content -LiteralPath $statusPath -Raw) -notmatch 'Stopped') {throw '人工停止状态未写入。'}
    Write-Host 'PASS 安装迁移、失败回退、LocalService 启停、独立工作进程和退出恢复实机验收通过。'
} finally {
    if($created -or (Get-TestService)) {
        & $env:SystemRoot\System32\sc.exe stop $serviceName 2>$null | Out-Null
        & $env:SystemRoot\System32\sc.exe delete $serviceName | Out-Null
        Start-Sleep 1
    }
    Remove-Item -LiteralPath $desktopConfig -Force -ErrorAction SilentlyContinue
    if((Test-Path -LiteralPath $dataDirectory) -and
       $dataDirectory.StartsWith($safeDataPrefix,[StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $dataDirectory -Recurse -Force
    }
    if((Test-Path -LiteralPath $installDirectory) -and
       $installDirectory.StartsWith($safeInstallPrefix,[StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $installDirectory -Recurse -Force
    }
}
