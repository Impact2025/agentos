$ErrorActionPreference = 'SilentlyContinue'
Write-Host "=== python* met zichtbaar console (MainWindowHandle!=0) ==="
$g = Get-Process -Name python* -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowHandle -ne 0 } |
    Select-Object Id, Name, MainWindowHandle,
        @{n='CLI';e={($_.CommandLine -replace '"','')[0..110] -join ''}}
if ($g) { $g | Format-Table -AutoSize | Out-String -Width 200 }
else { Write-Host "NONE met zichtbaar console" }

Write-Host "`n=== ALLE python* processen (MW=V=zichtbaar, .=onzichtbaar) ==="
Get-Process -Name python* -ErrorAction SilentlyContinue |
    Select-Object Id, Name, MainWindowHandle,
        @{n='MW';e={if($_.MainWindowHandle){'V'}else{'.'}}},
        @{n='CLI';e={($_.CommandLine -replace '"','')[0..95] -join ''}} |
    Format-Table -AutoSize | Out-String -Width 200
