param(
  [ValidateSet('Plan','Install','Activate','Status','Rollback','Uninstall')][string]$Mode='Plan',
  [string]$ServiceExecutable,
  [string]$DesktopConfig=(Join-Path $env:LOCALAPPDATA 'StratumSecureRelay\config.json'),
  [string]$InstallDirectory=(Join-Path $env:ProgramFiles 'MulinSenRelay'),
  [string]$DataDirectory=(Join-Path $env:ProgramData 'MulinSenRelayService'),
  [switch]$IsolatedTest,
  [string]$ExpectedTestThumbprint
)
$ErrorActionPreference='Stop'
$serviceName=$(if($IsolatedTest){'MulinSenRelaySupervisorTest'}else{'MulinSenRelaySupervisor'})
$targetExe=Join-Path $InstallDirectory $(if($IsolatedTest){'MulinSenRelayService.test.exe'}else{'MulinSenRelayService.exe'})
$manifestPath=Join-Path $DataDirectory 'install-manifest.json'
$runKey='HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$runName=$(if($IsolatedTest){'木林森中转隔离验收'}else{'木林森中转'})
if($IsolatedTest -and [String]::IsNullOrWhiteSpace($ExpectedTestThumbprint)) {throw '隔离验收缺少预期签名指纹。'}

function Assert-Administrator {
  $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
  $principal=[Security.Principal.WindowsPrincipal]$identity
  if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw '请右键“以管理员身份运行 PowerShell”，再执行此操作。'
  }
}
function New-RestrictedDirectory([string]$Path,[bool]$WritableByService) {
  $fullPath=[IO.Path]::GetFullPath($Path)
  if(Test-Path -LiteralPath $fullPath) {
    $existing=Get-Item -LiteralPath $fullPath -Force
    if(-not $existing.PSIsContainer -or ($existing.Attributes-band[IO.FileAttributes]::ReparsePoint)-ne0) {
      throw "服务目录不能是文件或目录链接：$fullPath"
    }
  }
  New-Item -ItemType Directory -Path $fullPath -Force | Out-Null
  $security=New-Object Security.AccessControl.DirectorySecurity
  $administrators=New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')
  $security.SetOwner($administrators)
  $security.SetAccessRuleProtection($true,$false)
  $inherit=[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
  $none=[Security.AccessControl.PropagationFlags]::None
  foreach($item in @(
    @('S-1-5-18',[Security.AccessControl.FileSystemRights]::FullControl),
    @('S-1-5-32-544',[Security.AccessControl.FileSystemRights]::FullControl),
    @('S-1-5-19',$(if($WritableByService){[Security.AccessControl.FileSystemRights]'Modify,Synchronize'}else{[Security.AccessControl.FileSystemRights]'ReadAndExecute,Read,Synchronize'}))
  )) {
    $sid=New-Object Security.Principal.SecurityIdentifier($item[0])
    $rule=New-Object Security.AccessControl.FileSystemAccessRule($sid,$item[1],$inherit,$none,[Security.AccessControl.AccessControlType]::Allow)
    $security.AddAccessRule($rule)
  }
  (Get-Item -LiteralPath $fullPath).SetAccessControl($security)
}
function Invoke-Sc([string[]]$Arguments,[int[]]$Allowed=@(0)) {
  $output=& $env:SystemRoot\System32\sc.exe @Arguments 2>&1
  if($Allowed -notcontains $LASTEXITCODE){throw "Windows 服务操作失败：$($Arguments[0]) $($Arguments[1])"}
  return $output
}
function Get-ServiceEntry {
  return Get-CimInstance Win32_Service -Filter "Name='$serviceName'" -ErrorAction SilentlyContinue
}
function Get-RunValue {
  if(-not(Test-Path -LiteralPath $runKey)){return $null}
  $properties=Get-ItemProperty -LiteralPath $runKey
  $property=$properties.PSObject.Properties[$runName]
  if($property){return $property.Value}
  return $null
}
function Assert-SignedService([string]$Path) {
  $file=Get-Item -LiteralPath $Path -ErrorAction Stop
  if($file.Extension -ne '.exe'){throw '服务程序文件名不正确。'}
  $signature=Get-AuthenticodeSignature -LiteralPath $file.FullName
  $valid=$(if($IsolatedTest){
      $file.Name -eq 'MulinSenRelayService.test.exe' -and $signature.SignerCertificate -and
      $signature.SignerCertificate.Subject -eq 'CN=木林森中转' -and
      $signature.SignerCertificate.Thumbprint -eq $ExpectedTestThumbprint
    }else{
      $signature.Status -eq 'Valid' -and $signature.SignerCertificate.Subject -eq 'CN=木林森中转' -and
      $signature.SignerCertificate.Thumbprint -eq 'D83C5C3E10822B6A4B45333EF41AFCC4F9156AAB'
    })
  if(-not $valid) {
    throw '服务程序数字签名无效或发布者不正确，已停止安装。'
  }
  return $file
}
function Save-Manifest([hashtable]$Value) {
  $temporary=$manifestPath+'.tmp'
  $Value | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $temporary -Encoding UTF8
  if(Test-Path -LiteralPath $manifestPath){[IO.File]::Replace($temporary,$manifestPath,$manifestPath+'.bak')}
  else{Move-Item -LiteralPath $temporary -Destination $manifestPath}
}
function Read-Manifest {
  if(-not (Test-Path -LiteralPath $manifestPath)){return $null}
  return Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
}
function Wait-ServiceState([string]$State,[int]$Seconds) {
  $until=(Get-Date).AddSeconds($Seconds)
  do {$entry=Get-ServiceEntry;if($entry -and $entry.State -eq $State){return $true};Start-Sleep -Milliseconds 500}while((Get-Date)-lt$until)
  return $false
}

if($Mode -eq 'Plan') {
  [pscustomobject]@{
    Result='只生成计划，未修改电脑';Service=$serviceName;InstallDirectory=$InstallDirectory;DataDirectory=$DataDirectory
    Steps=@('核对已签名服务程序和桌面配置','备份已有服务文件与加密配置','写入受限目录并生成机器级加密配置','注册为手动启动服务','桌面版退出后单独激活自动启动','失败时停用服务并恢复桌面版开机启动')
  } | ConvertTo-Json -Depth 4
  return
}

Assert-Administrator
if($Mode -eq 'Status') {
  $entry=Get-ServiceEntry
  $statusPath=Join-Path $DataDirectory 'service-status.txt'
  [pscustomobject]@{Installed=($null-ne$entry);State=$(if($entry){$entry.State}else{'未安装'});StartMode=$(if($entry){$entry.StartMode}else{''});
    Status=$(if(Test-Path -LiteralPath $statusPath){Get-Content -LiteralPath $statusPath -Raw}else{'暂无服务状态'})} | Format-List
  return
}

if($Mode -eq 'Install') {
  $source=Assert-SignedService $ServiceExecutable
  $config=Get-Item -LiteralPath $DesktopConfig -ErrorAction Stop
  if(($config.Attributes-band[IO.FileAttributes]::ReparsePoint)-ne0){throw '桌面配置不能使用文件链接。'}
  New-RestrictedDirectory $InstallDirectory $false
  New-RestrictedDirectory $DataDirectory $true
  $backupDirectory=Join-Path $DataDirectory ('backups\'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
  New-RestrictedDirectory $backupDirectory $true
  $previous=Get-ServiceEntry
  $manifest=@{InstalledAt=(Get-Date).ToString('o');BackupDirectory=$backupDirectory;PreviousService=($null-ne$previous);
    PreviousStartMode=$(if($previous){$previous.StartMode}else{''});PreviousRunValue=(Get-RunValue)}
  $managedPaths=@($targetExe,(Join-Path $DataDirectory 'service-config.dat'),(Join-Path $DataDirectory 'recovery.json'))
  $existingPaths=@{}
  foreach($path in $managedPaths) {
    $existingPaths[$path]=Test-Path -LiteralPath $path
    if(Test-Path -LiteralPath $path){Copy-Item -LiteralPath $path -Destination (Join-Path $backupDirectory ([IO.Path]::GetFileName($path))) -Force}
  }
  $staged=Join-Path $InstallDirectory (([IO.Path]::GetFileNameWithoutExtension($targetExe))+'.new.exe')
  try {
    if($previous -and $previous.State -ne 'Stopped') {
      Invoke-Sc @('stop',$serviceName) @(0,1062)|Out-Null
      if(-not(Wait-ServiceState 'Stopped' 20)){throw '旧服务无法停止。'}
    }
    Copy-Item -LiteralPath $source.FullName -Destination $staged -Force
    if((Get-FileHash $source.FullName -Algorithm SHA256).Hash-ne(Get-FileHash $staged -Algorithm SHA256).Hash){throw '服务程序复制校验失败。'}
    & $staged --prepare $config.FullName $DataDirectory
    if($LASTEXITCODE -ne 0){throw '桌面配置迁移失败，请确认使用保存配置的 Windows 用户执行。'}
    if(-not(Test-Path -LiteralPath (Join-Path $DataDirectory 'service-config.dat')) -or
       -not(Test-Path -LiteralPath (Join-Path $DataDirectory 'recovery.json'))) {
      throw '配置迁移程序未生成服务配置和恢复记录。'
    }
    & $staged --validate-prepared $DataDirectory
    if($LASTEXITCODE -ne 0){throw '服务配置验证失败。'}
    Move-Item -LiteralPath $staged -Destination $targetExe -Force
    $serviceCommand=$(if($IsolatedTest){"`"$targetExe`" --service-test `"$DataDirectory`""}else{"`"$targetExe`" --service"})
    if(-not $previous){Invoke-Sc @('create',$serviceName,'binPath=',$serviceCommand,'start=','demand','obj=','NT AUTHORITY\LocalService','DisplayName=','木林森中转服务')|Out-Null}
    else{Invoke-Sc @('config',$serviceName,'binPath=',$serviceCommand,'start=','demand','obj=','NT AUTHORITY\LocalService')|Out-Null}
    Invoke-Sc @('failure',$serviceName,'reset=','86400','actions=','restart/60000/restart/60000/restart/60000')|Out-Null
    Save-Manifest $manifest
    Write-Host '服务文件与加密配置已安全准备，但尚未启动。请退出桌面版后运行 -Mode Activate。'
  } catch {
    Remove-Item -LiteralPath $staged -Force -ErrorAction SilentlyContinue
    foreach($name in @([IO.Path]::GetFileName($targetExe),'service-config.dat','recovery.json')) {
      $saved=Join-Path $backupDirectory $name;$destination=$(if($name-eq[IO.Path]::GetFileName($targetExe)){$targetExe}else{Join-Path $DataDirectory $name})
      if(Test-Path -LiteralPath $saved){Copy-Item -LiteralPath $saved -Destination $destination -Force}
      elseif(-not $existingPaths[$destination]){Remove-Item -LiteralPath $destination -Force -ErrorAction SilentlyContinue}
    }
    if(-not $previous){Invoke-Sc @('delete',$serviceName) @(0,1060)|Out-Null}
    else {
      $startValue=$(if($previous.StartMode-eq'Auto'){'auto'}elseif($previous.StartMode-eq'Disabled'){'disabled'}else{'demand'})
      Invoke-Sc @('config',$serviceName,'start=',$startValue)|Out-Null
      if($previous.State-eq'Running'){Invoke-Sc @('start',$serviceName) @(0,1056)|Out-Null}
    }
    throw
  }
  return
}

if($Mode -eq 'Activate') {
  $manifest=Read-Manifest;if($null-eq$manifest){throw '尚未完成服务准备，请先运行 -Mode Install。'}
  & $targetExe --validate-prepared $DataDirectory;if($LASTEXITCODE -ne 0){throw '服务配置验证失败，不能激活。'}
  $desktop=@(Get-Process | Where-Object {$_.ProcessName-like'木林森中转*'})
  if($desktop.Count-gt0){throw '桌面版仍在运行。请在托盘选择“退出”，确认矿场维护窗口后再激活服务。'}
  $oldRun=Get-RunValue
  try {
    Remove-ItemProperty -Path $runKey -Name $runName -ErrorAction SilentlyContinue
    Invoke-Sc @('config',$serviceName,'start=','delayed-auto')|Out-Null
    Invoke-Sc @('start',$serviceName)|Out-Null
    if(-not(Wait-ServiceState 'Running' 30)){throw '服务未能进入运行状态。'}
    $until=(Get-Date).AddSeconds(90);$healthy=$false;$statusPath=Join-Path $DataDirectory 'service-status.txt'
    do {if(Test-Path $statusPath){$text=Get-Content $statusPath -Raw;if($text-match'Healthy'){$healthy=$true;break};if($text-match'Blocked'){break}};Start-Sleep 1}while((Get-Date)-lt$until)
    if(-not$healthy){throw '服务启动后未通过本地健康检查。'}
    Write-Host '木林森中转服务已启用，重启电脑后无需用户登录即可运行。'
  } catch {
    Invoke-Sc @('stop',$serviceName) @(0,1062)|Out-Null
    Invoke-Sc @('config',$serviceName,'start=','demand')|Out-Null
    if($oldRun){New-Item -Path $runKey -Force|Out-Null;Set-ItemProperty -Path $runKey -Name $runName -Value $oldRun}
    throw
  }
  return
}

if($Mode -in @('Rollback','Uninstall')) {
  $manifest=Read-Manifest
  Invoke-Sc @('stop',$serviceName) @(0,1060,1062)|Out-Null
  if(Get-ServiceEntry){Wait-ServiceState 'Stopped' 20|Out-Null;Invoke-Sc @('config',$serviceName,'start=','demand')|Out-Null}
  if($manifest -and $manifest.PreviousRunValue){New-Item -Path $runKey -Force|Out-Null;Set-ItemProperty -Path $runKey -Name $runName -Value $manifest.PreviousRunValue}
  if($Mode-eq'Uninstall'){Invoke-Sc @('delete',$serviceName) @(0,1060)|Out-Null;Write-Host '服务注册已删除；加密配置和备份仍保留，便于恢复。'}
  else{Write-Host '服务已停用，桌面版开机启动设置已恢复。'}
}
