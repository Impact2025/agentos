$ErrorActionPreference = 'SilentlyContinue'
$StartupDir = [Environment]::GetFolderPath('Startup')
$lnk = Join-Path $StartupDir 'AgentOS.lnk'
$target = 'D:\APPS\agentos\impactos_service.cmd'
Write-Host "lnk path: $lnk"
Write-Host "lnk exists: $(Test-Path $lnk)"
Write-Host "target exists: $(Test-Path $target)"
if ((Test-Path $lnk) -and (Test-Path $target)) {
    $w = New-Object -ComObject WScript.Shell
    $sc = $w.CreateShortcut($lnk)
    $sc.TargetPath = $target
    $sc.WorkingDirectory = 'D:\APPS\agentos'
    $sc.WindowStyle = 7
    $sc.Save()
    Write-Host "FIXED: AgentOS.lnk -> impactos_service.cmd (WindowStyle=7)"
}
