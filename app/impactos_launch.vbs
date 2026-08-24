' Impact OS - Mission Control launcher
' Start launch.ps1 volledig verborgen (geen flits van een console-venster).
' De PowerShell-launcher start Hermes gateway -> Hermes API -> Impact OS en opent de browser.
Set sh = CreateObject("WScript.Shell")
launcher = "D:\apps\impactos\launch.ps1"
sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & launcher & """", 0, False
