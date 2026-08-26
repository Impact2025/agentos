$targets = @{2652=1;7896=1;26108=1;26560=1;25296=1;1256=1;6452=1;7892=1;9788=1;12924=1;15796=1;16204=1;18648=1;19980=1;22092=1;22444=1;22836=1;25316=1;768=1;5964=1}
$parents = @{}
Get-WmiObject Win32_Process | ForEach-Object { $parents[$_.ProcessId] = $_ }

function ShortCli([string]$s, [int]$n) {
  if (-not $s) { return "<none>" }
  if ($s.Length -le $n) { return $s }
  return $s.Substring(0, $n) + "..."
}

foreach ($p in $parents.Values) {
    $name = $p.Name
    if (-not ($targets.ContainsKey($p.ProcessId) -or $name -like 'python*' -or $name -eq 'cmd' -or $name -eq 'powershell')) {
        continue
    }
    $ppid = $p.ParentProcessId
    $par = $parents[$ppid]
    $cli = ShortCli $p.CommandLine 130
    if ($par) {
        $parcli = ShortCli $par.CommandLine 90
        $parname = $par.Name
    } else {
        $parcli = "<none>"
        $parname = "<root>"
    }
    Write-Output ("PID={0} PPID={1} name={2} parent={3}" -f $p.ProcessId, $ppid, $name, $parname)
    Write-Output ("   CLI: $cli")
    Write-Output ("   PCLI: $parcli")
    Write-Output ("")
}
