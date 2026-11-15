# SPDX-License-Identifier: Apache-2.0
# Copyright 2025-2026 fiyo (Jack Ge) <sdfiyon@gmail.com>
#
# RaccoonX remote host collector for Windows targets.
#
# The whole file is piped to stdin by the center host over an existing SSH
# session; no file is written on the target and no extra dependency is needed
# (PowerShell 3+ / CIM / Get-Process ship with Windows).
#
# Launcher used by modules/pro/metrics_collector.py:
#   powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command -
#
# Optional config is injected by the center host as a single extra first line:
#   $RxConfigB64 = '<base64 of a UTF-8 JSON object>'
# Supported keys: service_patterns (array of -like wildcards), sample_ms.
#
# Output: exactly ONE line of compact JSON on stdout. Diagnostics go to stderr.
# Requires PowerShell 3.0+ ([ordered] hashtables). Windows Server 2012+ ships it.
# NOTE: keep this file pure ASCII. Windows PowerShell 5.1 reads piped script
# text using the console ANSI codepage, so non-ASCII source would be mangled.

$ErrorActionPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
try { $OutputEncoding = [Text.Encoding]::UTF8 } catch {}

# CIM cmdlets are missing on PowerShell 2.0 and can be blocked by policy on
# hardened hosts; Get-WmiObject is the always-present equivalent. Probe once.
$hasCim = $false
try { if (Get-Command Get-CimInstance -ErrorAction Stop) { $hasCim = $true } } catch {}

function Get-WmiObj([string]$class, [string]$filter = '') {
    if ($hasCim) {
        if ($filter) { return Get-CimInstance -ClassName $class -Filter $filter }
        return Get-CimInstance -ClassName $class
    }
    if ($filter) { return Get-WmiObject -Class $class -Filter $filter }
    return Get-WmiObject -Class $class
}

# -- Config ----------------------------------------------------------------
$patterns = @()
$sampleMs = 900
$explicitCfg = $false
try {
    if ($RxConfigB64) {
        $cfgJson = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($RxConfigB64))
        $cfg = ConvertFrom-Json -InputObject $cfgJson
        if ($cfg.service_patterns) { $patterns = @($cfg.service_patterns) }
        if ($cfg.sample_ms) { $sampleMs = [int]$cfg.sample_ms }
        if ($cfg.explicit) { $explicitCfg = [bool]$cfg.explicit }
    }
} catch { $patterns = @() }
if ($sampleMs -lt 200) { $sampleMs = 200 }
if ($sampleMs -gt 3000) { $sampleMs = 3000 }

# Default: database-related Windows services. Users can override with an
# explicit list on the datasource form (comma separated, * wildcard allowed).
if ($patterns.Count -eq 0) {
    $patterns = @(
        'MSSQLSERVER', 'MSSQL$*', 'SQLSERVERAGENT', 'SQLAgent$*',
        'OracleService*', 'OracleOraDb*TNSListener*', 'Oracle*Listener*',
        'DmService*', 'DmAPService*', 'Dm*Watcher*',
        'MySQL*', 'MariaDB*', 'PostgreSQL*', 'postgresql*',
        'Kingbase*', 'highgo*', 'hgdb*', 'IvorySQL*', 'UXDB*', 'vastbase*',
        'MongoDB*', 'mongod*', 'Redis*',
        'OceanBase*', 'observer*', 'GBase*', 'ClickHouse*'
    )
}

function Test-ServicePattern([string]$name, $pats) {
    foreach ($p in $pats) {
        if ($name -like $p) { return $true }
    }
    return $false
}

# CPU time snapshots keyed by PID. TotalProcessorTime is a cumulative counter,
# so a two-sample delta / wall time gives a per-core CPU percentage. This is
# locale independent (unlike Get-Counter, whose paths are localized on zh-CN).
function Get-CpuTimeMap {
    $m = @{}
    foreach ($p in (Get-Process)) {
        try { $m[[int]$p.Id] = [double]$p.TotalProcessorTime.TotalSeconds } catch {}
    }
    return $m
}

function Get-ProcInfoMap {
    $m = @{}
    foreach ($p in (Get-Process)) {
        try {
            $m[[int]$p.Id] = @{
                mem  = [double]$p.WorkingSet64
                name = [string]$p.ProcessName
            }
        } catch {}
    }
    return $m
}

# -- Whole machine (same field names as remote_host_shell.sh) ---------------
$cpuCount = 0
try { $cpuCount = [int][Environment]::ProcessorCount } catch {}
$cpuModel = 'unknown'
try {
    $cpuObj = Get-WmiObj 'Win32_Processor' | Select-Object -First 1
    if ($cpuObj -and $cpuObj.Name) { $cpuModel = [string]$cpuObj.Name }
} catch {}

$cpuPct = 0.0
try {
    $perfCpu = Get-WmiObj 'Win32_PerfFormattedData_PerfOS_Processor' "Name='_Total'"
    if ($perfCpu) { $cpuPct = [double]$perfCpu.PercentProcessorTime }
} catch {}
if (-not $cpuPct) {
    try {
        $avg = Get-WmiObj 'Win32_Processor' | Measure-Object -Property LoadPercentage -Average
        if ($avg -and $avg.Average) { $cpuPct = [double]$avg.Average }
    } catch {}
}

$memTotalB = 0.0; $memAvailB = 0.0; $memUsedB = 0.0; $memPct = 0.0
try {
    $osObj = Get-WmiObj 'Win32_OperatingSystem'
    $memTotalB = [double]$osObj.TotalVisibleMemorySize * 1024
    $memAvailB = [double]$osObj.FreePhysicalMemory * 1024
    $memUsedB = $memTotalB - $memAvailB
    if ($memUsedB -lt 0) { $memUsedB = 0 }
    if ($memTotalB -gt 0) { $memPct = $memUsedB / $memTotalB * 100.0 }
} catch {}

$swapTotalB = 0.0; $swapUsedB = 0.0; $swapPct = 0.0
try {
    foreach ($pf in (Get-WmiObj 'Win32_PageFileUsage')) {
        $swapTotalB += [double]$pf.AllocatedBaseSize * 1048576
        $swapUsedB += [double]$pf.CurrentUsage * 1048576
    }
    if ($swapTotalB -gt 0) { $swapPct = $swapUsedB / $swapTotalB * 100.0 }
} catch {}

$diskReadBytes = 0.0; $diskWriteBytes = 0.0
$diskReadCount = 0.0; $diskWriteCount = 0.0
$diskReadMs = 0.0; $diskWriteMs = 0.0
try {
    $pd = Get-WmiObj 'Win32_PerfRawData_PerfDisk_PhysicalDisk' "Name='_Total'"
    if ($pd) {
        $diskReadBytes = [double]$pd.DiskReadBytesPerSec
        $diskWriteBytes = [double]$pd.DiskWriteBytesPerSec
        $diskReadCount = [double]$pd.DiskReadsPerSec
        $diskWriteCount = [double]$pd.DiskWritesPerSec
        # PerfRawData time counters are cumulative 100ns ticks -> milliseconds
        $diskReadMs = [double]$pd.PercentDiskReadTime / 10000
        $diskWriteMs = [double]$pd.PercentDiskWriteTime / 10000
    }
} catch {}

# -- Two-sample CPU window (shared by machine + per-service deltas) ---------
$t0 = Get-Date
$cpu0 = Get-CpuTimeMap
Start-Sleep -Milliseconds $sampleMs
$cpu1 = Get-CpuTimeMap
$procInfo = Get-ProcInfoMap
$t1 = Get-Date
$dtSec = ($t1 - $t0).TotalSeconds
if ($dtSec -le 0) { $dtSec = 1.0 }

# -- Windows services ------------------------------------------------------
$allServices = @()
try { $allServices = @(Get-WmiObj 'Win32_Service') } catch {}

# How many services share each PID. svchost.exe hosts many services, so its
# CPU / working set cannot be split per service; we surface that explicitly
# instead of pretending the numbers are service-exclusive.
$servicesPerPid = @{}
foreach ($s in $allServices) {
    $svcPid = 0
    try { $svcPid = [int]$s.ProcessId } catch {}
    if ($svcPid -gt 0) {
        if ($servicesPerPid.ContainsKey($svcPid)) { $servicesPerPid[$svcPid]++ }
        else { $servicesPerPid[$svcPid] = 1 }
    }
}

$services = New-Object System.Collections.ArrayList
foreach ($s in $allServices) {
    if (-not (Test-ServicePattern ([string]$s.Name) $patterns)) { continue }

    $svcPid = 0
    try { $svcPid = [int]$s.ProcessId } catch {}
    $memB = 0.0
    $procName = ''
    $svcCpuPct = 0.0
    if ($svcPid -gt 0) {
        if ($procInfo.ContainsKey($svcPid)) {
            $memB = [double]$procInfo[$svcPid].mem
            $procName = [string]$procInfo[$svcPid].name
        }
        if ($cpu0.ContainsKey($svcPid) -and $cpu1.ContainsKey($svcPid)) {
            $delta = [double]$cpu1[$svcPid] - [double]$cpu0[$svcPid]
            if ($delta -lt 0) { $delta = 0 }
            $svcCpuPct = $delta / $dtSec * 100.0
        }
    }
    $sharedCount = 1
    if ($svcPid -gt 0 -and $servicesPerPid.ContainsKey($svcPid)) {
        $sharedCount = [int]$servicesPerPid[$svcPid]
    }
    $state = [string]$s.State
    $startMode = [string]$s.StartMode

    [void]$services.Add([ordered]@{
        name            = [string]$s.Name
        display_name    = [string]$s.DisplayName
        status          = $state
        start_mode      = $startMode
        pid             = $svcPid
        process_name    = $procName
        cpu_pct         = [math]::Round($svcCpuPct, 1)
        mem_mb          = [math]::Round($memB / 1048576, 1)
        mem_bytes       = [long]$memB
        shared_process  = [bool]($sharedCount -gt 1)
        shared_services = $sharedCount
        explicit        = [bool]$explicitCfg
    })
}

# Aggregate CPU / memory over UNIQUE pids, otherwise services sharing one
# svchost.exe would be summed several times and inflate the totals.
$seenPids = @{}
$aggCpu = 0.0; $aggMemB = 0.0
foreach ($svc in $services) {
    $p = [int]$svc.pid
    if ($p -gt 0 -and -not $seenPids.ContainsKey($p)) {
        $seenPids[$p] = $true
        $aggCpu += [double]$svc.cpu_pct
        $aggMemB += [double]$svc.mem_bytes
    }
}
$runningCount = 0; $stoppedCount = 0; $disabledStopped = 0; $autoAbnormal = 0
foreach ($svc in $services) {
    if ($svc.status -eq 'Running') { $runningCount++ }
    else {
        if ($svc.start_mode -eq 'Disabled') { $disabledStopped++ }
        else {
            $stoppedCount++
            # An automatic service that is not running is a real anomaly.
            # A manually started service being stopped is normal, unless the
            # user explicitly listed it on the datasource form.
            if ($svc.start_mode -eq 'Auto' -or $svc.explicit) { $autoAbnormal++ }
        }
    }
}

# -- Output (single compact JSON line) -------------------------------------
$out = [ordered]@{
    host_collector_source   = 'powershell'
    host_os                 = 'windows'
    host_cpu                = [math]::Round($cpuPct, 1)
    host_cpu_iowait         = 0
    host_cpu_count          = $cpuCount
    host_cpu_model          = $cpuModel
    host_mem_total          = [long]$memTotalB
    host_mem_used           = [long]$memUsedB
    host_mem_available      = [long]$memAvailB
    host_mem                = [math]::Round($memPct, 1)
    host_mem_used_gb        = [math]::Round($memUsedB / 1073741824, 1)
    host_mem_total_gb       = [math]::Round($memTotalB / 1073741824, 1)
    host_swap_total         = [long]$swapTotalB
    host_swap_used          = [long]$swapUsedB
    host_swap_pct           = [math]::Round($swapPct, 1)
    host_swap               = [math]::Round($swapPct, 1)
    host_disk_read_bytes    = [long]$diskReadBytes
    host_disk_write_bytes   = [long]$diskWriteBytes
    host_disk_read_count    = [long]$diskReadCount
    host_disk_write_count   = [long]$diskWriteCount
    host_disk_read_ms       = [long]$diskReadMs
    host_disk_write_ms      = [long]$diskWriteMs
    host_services           = $services
    host_services_count     = $services.Count
    host_services_running   = $runningCount
    host_services_stopped   = ($stoppedCount + $disabledStopped)
    host_services_abnormal  = $autoAbnormal
    host_services_cpu_pct   = [math]::Round($aggCpu, 1)
    host_services_mem_mb    = [math]::Round($aggMemB / 1048576, 1)
    host_services_shared    = $seenPids.Count
    host_service_sample_ms  = [int]($dtSec * 1000)
}

Write-Output ($out | ConvertTo-Json -Compress -Depth 8)
