# rag_proc_check.ps1 — monitor dev_rag.mcp_server processes (plan 003 acceptance).
#
# Run between open/close cycles of ZCode:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\rag_proc_check.ps1
#
# Shows: how many live RAG instances, who owns each, and which are ORPHANS
# (dead/unresolvable owner) — exactly the ones process_guard must kill at the
# next startup of a new instance.

$marker = '-m dev_rag.mcp_server'
$py = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'"
$marked = $py | Where-Object { $_.CommandLine -match [regex]::Escape($marker) }

if (-not $marked) {
    Write-Host "No RAG processes." -ForegroundColor Green
    return
}

# Which marked-process pids are stubs (their pid is someone's ppid among marked).
$markedPids = @($marked | ForEach-Object { $_.ProcessId })
$ppids = @($marked | ForEach-Object { $_.ParentProcessId })
$stubPids = @($marked | Where-Object { $ppids -contains $_.ProcessId } | ForEach-Object { $_.ProcessId })

Write-Host ""
Write-Host "=== dev_rag.mcp_server processes ===" -ForegroundColor Cyan
$rows = foreach ($p in $marked) {
    $isStub = if ($stubPids -contains $p.ProcessId) { 'stub' } else { 'real' }
    # Owner = nearest ancestor OUTSIDE marked-pids (walk up via ppid).
    $ownerName = '(unresolvable — ORPHAN)'
    $ownerPid  = 0
    $cur = $p.ParentProcessId
    for ($i = 0; $i -lt 10; $i++) {
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$cur" -ErrorAction SilentlyContinue
        if (-not $parent) { break }   # ancestor dead
        if ($markedPids -notcontains $cur) {
            $ownerPid  = $cur
            $ownerName = $parent.Name
            break
        }
        $cur = $parent.ParentProcessId
    }
    [PSCustomObject]@{
        PID    = $p.ProcessId
        Role   = $isStub
        PPID   = $p.ParentProcessId
        Owner  = if ($ownerPid) { "$ownerPid ($ownerName)" } else { 'DEAD/ORPHAN' }
        Created = $p.CreationDate
    }
}
$rows | Format-Table -AutoSize

$realCount   = @($rows | Where-Object { $_.Role -eq 'real' }).Count
$orphanCount = @($rows | Where-Object { $_.Role -eq 'real' -and $_.Owner -like 'DEAD*' }).Count
Write-Host ("real instances: {0}" -f $realCount)
Write-Host ("  ORPHANS (should be killed at next RAG startup): {0}" -f $orphanCount) -ForegroundColor $(if ($orphanCount -gt 0) { 'Yellow' } else { 'Green' })
Write-Host ""
Write-Host "plan 003 expectation:" -ForegroundColor Cyan
Write-Host "  - clients open   : real == number of clients (ZCode/Claude/VSCode)."
Write-Host "  - client closes  : orphans may briefly appear (real with dead owner)."
Write-Host "  - client reopens : orphans gone (process_guard cleaned them)."
