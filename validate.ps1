$ErrorActionPreference = 'Stop'

Write-Host "=== 1. Schieten van Triage Request naar Agent OS ===" -ForegroundColor Cyan
$body = @{
    prompt = "Schrijf een SEO blog over AI in de zorg"
    workspace_path = "weareimpact/seo/blog-ai-zorg.md"
} | ConvertTo-Json

$r = Invoke-RestMethod -Method POST -Uri "http://127.0.0.1:1250/api/tasks/triage" -ContentType 'application/json' -Body $body

Write-Host "Project aangemaakt: $($r.project_name)" -ForegroundColor Green
$r.subtasks | ForEach-Object { 
    Write-Host "$($_.status) | $($_.title) -> $($_.assigned_to)" 
}

Write-Host "`n=== 2. Wachten op de Conveyor Loop (8 seconden) ===" -ForegroundColor Yellow
Start-Sleep -Seconds 8

Write-Host "=== 3. Controleren van de Live Status van de Taken ===" -ForegroundColor Cyan
$tasks = Invoke-RestMethod -Uri "http://127.0.0.1:1250/api/tasks"
$tasks | ForEach-Object { 
    Write-Host "$($_.status) | $($_.title) | depends_on: $($_.depends_on_index)" 
}
