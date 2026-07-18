# Agent OS — Mission Control

Altijd-draaiende lokale launcher die de "Agent OS" knop laat werken, ook als de
server uitstaat. Als je op de knop klikt terwijl Agent OS (of Hermes) down is,
start de launcher ze automatisch op en navigeert daarna naar de app.

## Wat is er opgelost
De oude `agentos_service.cmd` had een haakje `)` in een `echo`-regel. CMD las die
`)` als einde van een codeblok, waardoor het hele script crashte VÓÓR de uvicorn-
regel. Gevolg: de server startte nooit meer -> `ERR_CONNECTION_REFUSED` op
localhost:1250. Deze bug is gefixt.

## Missie Controle pagina
- Open:  http://127.0.0.1:8088
- Knop "Open Agent OS": start Hermes + Agent OS server als ze down zijn, daarna
  redirect naar http://localhost:1250

## Bestanden
- launcher/server.py            -> de altijd-draaiende launcher (poort 8088)
- launcher/mission_control.vbs  -> start de launcher bij Windows-aanmelding (geen admin)
- agentos_service.cmd           -> (gefikst) start de Agent OS backend op 1250
- setup_launcher_task.ps1       -> optioneel: Windows Taak Scheduler (wel admin)

## Automatisch starten bij opstart (geen admin nodig)
De `mission_control.vbs` ligt al in de Windows Startup-map
(%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup). Bij elke aanmelding
start de launcher op de achtergrond. Er staat ook een snelkoppeling
"Mission Control" op je bureaublad.

Handmatig starten:
    wscript "D:\apps\agentos\launcher\mission_control.vbs"

## Extra robuustheid (optioneel, wel administrator)
Voer eenmalig uit als Administrator:
    powershell -ExecutionPolicy Bypass -File D:\apps\agentos\setup_launcher_task.ps1
Dit registreert een Taak Scheduler-taak die de launcher bij aanmelding start en
bij een crash binnen 1 minuut opnieuw opstart.

## Je bestaande "Agent OS" knop / bladwijzer
Wijs die knop of bladwijzer naar http://127.0.0.1:8088 in plaats van
http://localhost:1250. Dan start alles automatisch bij één klik.
