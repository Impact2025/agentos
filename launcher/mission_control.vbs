' Mission Control launcher - op de achtergrond starten bij Windows-aanmelding.
' Geen admin nodig: plaats een verwijzing naar dit .vbs in de Startup-map
' (shell:startup) of voer het eenmalig uit. Het start de Python launcher
' verborgen; die blijft op 127.0.0.1:8088 draaien en biedt de Impact OS-knop.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

Py    = "D:\apps\impactos\.venv\Scripts\python.exe"
Launcher = "D:\apps\impactos\launcher\server.py"

If Not fso.FileExists(Py) Then
    WScript.Echo "Python venv niet gevonden: " & Py
    WScript.Quit 1
End If
If Not fso.FileExists(Launcher) Then
    WScript.Echo "Launcher niet gevonden: " & Launcher
    WScript.Quit 1
End If

' Alleen starten als er nog geen listener op 8088 is.
On Error Resume Next
Set sock = CreateObject("WScript.Network")
Err.Clear
Dim up : up = False
On Error GoTo 0

' Voorkom dubbele instanties: check of poort 8088 al in gebruik is.
Set oShell = CreateObject("WScript.Shell")
Dim cmd : cmd = "powershell.exe -NoProfile -Command ""if(Get-NetTCPConnection -LocalPort 8088 -State Listen -ErrorAction SilentlyContinue){exit 0}else{exit 1}"""
rc = oShell.Run(cmd, 0, True)
If rc = 0 Then
    ' Poort is al in gebruik -> niets doen.
    WScript.Quit 0
End If

' Start de launcher verborgen (0 = hidden window style).
sh.Run """" & Py & """ """ & Launcher & """", 0, False
