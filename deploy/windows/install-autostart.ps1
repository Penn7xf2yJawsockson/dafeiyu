# 登录 Windows 后自动启动大肥鱼(工单 03)。不需要管理员权限。
# 用法(在 WSL 里):powershell.exe -ExecutionPolicy Bypass -File "$(wslpath -w deploy/windows/install-autostart.ps1)"
# 卸载:Unregister-ScheduledTask -TaskName "dafeiyu-autostart" -Confirm:$false

$ErrorActionPreference = "Stop"
$Distro = "Ubuntu"
$Script = "/home/admini/dafeiyu/deploy/wsl-keepalive.sh"
$Dir = Join-Path $env:LOCALAPPDATA "dafeiyu"
$Vbs = Join-Path $Dir "start-dafeiyu.vbs"

New-Item -ItemType Directory -Force -Path $Dir | Out-Null
# 用 VBS 启动是为了完全不弹窗口(窗口样式 0);wsl.exe 常驻,WSL 就不会空闲关闭
@"
CreateObject("WScript.Shell").Run "wsl.exe -d $Distro -u root -- bash $Script", 0, False
"@ | Set-Content -Encoding ASCII -Path $Vbs

$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$Vbs`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "dafeiyu-autostart" -Action $action -Trigger $trigger -Settings $settings `
    -Description "登录后启动 WSL、Docker 与大肥鱼记忆网关,并保持 WSL 常驻" -Force | Out-Null

Write-Output "已注册计划任务 dafeiyu-autostart(登录时运行):$Vbs"
