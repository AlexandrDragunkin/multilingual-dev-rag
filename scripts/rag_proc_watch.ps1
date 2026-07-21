# rag_proc_watch.ps1 - watch RAG python processes in real time.
# Logs every appearance/disappearance of dev_rag.mcp_server with timestamps.
# Run in background while user opens/closes ZCode.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\rag_proc_watch.ps1
#
# Output goes to scripts\rag_proc_watch.log (appended). Stop with Ctrl+C.

$marker = '-m dev_rag.mcp_server'
$log = 'C:\REPO\multilingual-dev-rag\scripts\rag_proc_watch.log'
$stamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
Add-Content -Path $log -Value "=== watch started $stamp ===" -Encoding UTF8

$prev = @{}
for ($i = 0; $i -lt 240; $i++) {   # 240 x 5s = 20 min
    $py = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue
    $marked = @($py | Where-Object { $_.CommandLine -match [regex]::Escape($marker) })
    $now = @{}
    foreach ($p in $marked) { $now[$p.ProcessId] = $p }
    $nowPids = $now.Keys
    $prevPids = $prev.Keys

    foreach ($pid_new in $nowPids) {
        if ($prevPids -notcontains $pid_new) {
            $p = $now[$pid_new]
            $t = (Get-Date).ToString('HH:mm:ss')
            $line = ("$t  +START pid=$pid_new ppid=$($p.ParentProcessId) created=$($p.CreationDate)")
            Add-Content -Path $log -Value $line -Encoding UTF8
            Write-Host $line
        }
    }
    foreach ($pid_old in $prevPids) {
        if ($nowPids -notcontains $pid_old) {
            $t = (Get-Date).ToString('HH:mm:ss')
            $line = ("$t  -GONE  pid=$pid_old")
            Add-Content -Path $log -Value $line -Encoding UTF8
            Write-Host $line
        }
    }
    $prev = $now
    Start-Sleep -Seconds 5
}

$stamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
Add-Content -Path $log -Value "=== watch ended $stamp ===" -Encoding UTF8
Write-Host "=== watch window elapsed (20 min) ==="
