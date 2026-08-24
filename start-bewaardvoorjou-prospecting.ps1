$ErrorActionPreference = 'Stop'

Write-Host "=== 1. Aanmaken B2B Prospecting Taakketen ===" -ForegroundColor Cyan
$body = @{
    prompt = "Start B2B-prospecting voor Bewaard Voor Jou: zoek notariskantoren, uitvaartondernemers en zorginstellingen in Nederland via browserautomatisering, verrijk zakelijke contactgegevens, genereer gepersonaliseerde concept-outreach, en controleer alles handmatig voordat het verzonden wordt."
    workspace_path = "projects/bewaardvoorjou/prospecting/run-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
} | ConvertTo-Json -Compress

try {
    $r = Invoke-RestMethod -Method POST -Uri "http://127.0.0.1:1250/api/tasks/triage" -ContentType 'application/json' -Body $body
} catch {
    Write-Host "FOUT: Kan Impact OS niet bereiken op http://127.0.0.1:1250" -ForegroundColor Red
    Write-Host $_.Exception.Message
    exit 1
}

Write-Host "Project: $($r.project_name)" -ForegroundColor Green
Write-Host "Aangemaakte taken:" -ForegroundColor Yellow
$r.subtasks | ForEach-Object { 
    Write-Host "  $($_.status) | $($_.title) -> $($_.assigned_to) (depends_on: $($_.depends_on_index))" 
}

Write-Host ""
Write-Host "=== 2. Dictionary met taak ID's ===" -ForegroundColor Cyan
$r.subtasks | ForEach-Object { 
    Write-Host "$($_.assigned_to): $($_.id)" 
}

Write-Host ""
Write-Host "Volgende stappen:" -ForegroundColor Magenta
Write-Host "1. Open http://localhost:1250/docs voor live monitoring"
Write-Host "2. Eerste taak zal automatisch naar 'running' gaan via de conveyor loop"
Write-Host "3. Controleer de gedeelde workspace voor gegenereerde output"
Write-Host "4. Keur concept-outreach handmatig goed voordat verzending"
