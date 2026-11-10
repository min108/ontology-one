# CI-only administrator collector. Never shipped in the application package.
# Official CLI: https://learn.microsoft.com/troubleshoot/windows-client/shell-experience/troubleshoot-apps-start-failure-use-process-monitor
param(
    [Parameter(Mandatory=$true)][string]$Package,
    [Parameter(Mandatory=$true)][string]$Reports,
    [Parameter(Mandatory=$true)][System.Management.Automation.PSCredential]$Credential
)
$ErrorActionPreference = 'Stop'
$report = Join-Path $Reports 'loader-capture.json'
$private = Join-Path $env:RUNNER_TEMP ('ontology-loader-private-' + [Guid]::NewGuid().ToString('N'))
$procmon = $null
$capturing = $false
$phase = 'private_directory'
$outcome = @{ schema_version = 1; status = 'collector_unavailable'; raw_content_retained = $false }
function Run-Bounded([string]$File, [string[]]$Arguments, [int]$Seconds) {
    $child = Start-Process -FilePath $File -ArgumentList $Arguments -PassThru -RedirectStandardOutput (Join-Path $private 'tool.stdout') -RedirectStandardError (Join-Path $private 'tool.stderr')
    if (-not $child.WaitForExit($Seconds * 1000)) {
        $child.Kill(); $child.WaitForExit(); throw 'COLLECTOR_COMMAND_TIMEOUT'
    }
    $child.Refresh()
    if ($child.ExitCode -ne 0) { throw 'COLLECTOR_COMMAND_FAILED' }
}
function Read-OwnedLoaderEvents([string]$Receipt, [datetime]$Start, [datetime]$End) {
    $allowed = @((Get-Content -LiteralPath $Receipt -Raw | ConvertFrom-Json).child_pids)
    $summaries = @()
    foreach ($source in @(@{ name='Application'; label='side_by_side'; provider='SideBySide' },
                         @{ name='Microsoft-Windows-CodeIntegrity/Operational'; label='code_integrity'; provider=$null })) {
        try {
            $log = Get-WinEvent -ListLog $source.name -ErrorAction Stop
            if (-not $log.IsEnabled) { $summaries += @{ source=$source.label; status='disabled'; events=@() }; continue }
            $filter = @{ LogName=$source.name; StartTime=$Start; EndTime=$End }
            if ($source.provider) { $filter.ProviderName=$source.provider }
            $events = @()
            $records = @(Get-WinEvent -FilterHashtable $filter -MaxEvents 256 -ErrorAction SilentlyContinue)
            foreach ($record in $records) {
                [xml]$xml = $record.ToXml()
                $ids = @([string]$xml.Event.System.Execution.ProcessID)
                foreach ($data in $xml.Event.EventData.Data) {
                    if ($data.Name -in @('ProcessId','ProcessID','TargetProcessId','TargetProcessID')) { $ids += [string]$data.InnerText }
                }
                $matched = @($ids | Where-Object { $_ -match '^\d+$' -and [uint64]$_ -in $allowed })
                if (-not $matched.Count) { continue }
                $statuses = @()
                foreach ($data in $xml.Event.EventData.Data) {
                    $value = [string]$data.InnerText
                    if ($data.Name -in @('Status','StatusCode','ErrorCode') -and $value -match '^(0x[0-9a-fA-F]{1,8}|[0-9]{1,10})$') {
                        $statuses += [Convert]::ToUInt32($value, $(if ($value.StartsWith('0x')) { 16 } else { 10 }))
                    }
                }
                $events += @{ event_id=[int]$record.Id; level=[int]$record.Level; status_codes=@($statuses | Select-Object -First 4) }
                if ($events.Count -ge 32) { break }
            }
            $summaries += @{ source=$source.label; status='queried'; events=$events }
        } catch { $summaries += @{ source=$source.label; status='unavailable'; events=@() } }
    }
    return $summaries
}
try {
    if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'ADMIN_REQUIRED' }
    if (Get-Process -Name Procmon,Procmon64,Procmon64a -ErrorAction SilentlyContinue) { throw 'PREEXISTING_COLLECTOR' }
    $null = New-Item -ItemType Directory -Path $private
    $current = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $security = New-Object Security.AccessControl.DirectorySecurity
    $security.SetAccessRuleProtection($true, $false)
    $security.SetOwner($current)
    foreach ($principal in @($current, [Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
        $security.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($principal, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
    }
    Set-Acl -LiteralPath $private -AclObject $security
    $phase = 'official_download'
    $archive = Join-Path $private 'ProcessMonitor.zip'
    Invoke-WebRequest -Uri 'https://download.sysinternals.com/files/ProcessMonitor.zip' -OutFile $archive -TimeoutSec 45
    # Microsoft winget manifest, ProcessMonitor 4.13, published 2026-11-05.
    # https://github.com/microsoft/winget-pkgs/blob/master/manifests/m/Microsoft/Sysinternals/ProcessMonitor/4.13/Microsoft.Sysinternals.ProcessMonitor.installer.yaml
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne '97330813853E0E514A11EE16A47D4C6FE90148FAE834649839AFB30487CC8B0B') { throw 'COLLECTOR_HASH_MISMATCH' }
    $phase = 'publisher_verification'
    Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $private 'tool')
    $procmon = Join-Path $private 'tool/Procmon64.exe'
    $signature = Get-AuthenticodeSignature -LiteralPath $procmon
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') { throw 'COLLECTOR_SIGNATURE_INVALID' }
    $pml = Join-Path $private 'capture.pml'
    $csv = Join-Path $private 'capture.csv'
    $phase = 'capture_start'
    $captureStart = [datetime]::UtcNow
    $capture = Start-Process -FilePath $procmon -ArgumentList @('/AcceptEula','/Quiet','/Minimized','/NoFilter','/BackingFile',"`"$pml`"",'/Runtime','20') -PassThru -RedirectStandardOutput (Join-Path $private 'capture.stdout') -RedirectStandardError (Join-Path $private 'capture.stderr')
    $capturing = $true
    Run-Bounded $procmon @('/WaitForIdle','/Quiet') 8
    $phase = 'fixed_probe'
    $receipt = Join-Path $Reports 'lpac-probe-receipt.json'
    $python = Join-Path $Package 'runtime/python/python.exe'
    $entry = Join-Path $Package 'tests/windows_lpac_probe.py'
    $arguments = @('-I','-X','utf8','-B',"`"$entry`"",'--receipt',"`"$receipt`"")
    $probe = Start-Process -FilePath $python -ArgumentList $arguments -Credential $Credential -LoadUserProfile -WorkingDirectory $Package -PassThru -RedirectStandardOutput (Join-Path $private 'probe.stdout') -RedirectStandardError (Join-Path $private 'probe.stderr')
    if (-not $probe.WaitForExit(30000)) {
        # Only this owned probe handle is affected; its Job kills descendants.
        $probe.Kill(); $probe.WaitForExit(); throw 'PROBE_TIMEOUT'
    }
    $phase = 'capture_stop'
    Run-Bounded $procmon @('/Terminate','/Quiet') 10
    $capturing = $false
    if (-not $capture.WaitForExit(10000)) { throw 'CAPTURE_EXIT_TIMEOUT' }
    $phase = 'csv_export'
    Run-Bounded $procmon @('/AcceptEula','/Quiet','/OpenLog',"`"$pml`"",'/SaveAs',"`"$csv`"") 30
    $summary = Join-Path $PSScriptRoot 'summarize_windows_loader.py'
    $phase = 'safe_summary'
    & $python -I -X utf8 -B $summary --csv $csv --receipt $receipt --report $report
    if ($LASTEXITCODE -ne 0) { throw 'SUMMARY_FAILED' }
    $outcome = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json -AsHashtable
    $outcome.existing_loader_event_logs = @(Read-OwnedLoaderEvents $receipt $captureStart ([datetime]::UtcNow))
    $outcome.collector_version = '4.13'
    $outcome.collector_archive_sha256 = '97330813853e0e514a11ee16a47d4c6fe90148fae834649839afb30487cc8b0b'
} catch {
    # Never output exception text: Windows exceptions can contain raw paths.
    $outcome = @{ schema_version = 1; status = 'collector_unavailable'; phase = $phase; raw_content_retained = $false }
} finally {
    if ($capturing -and $procmon) {
        try { Run-Bounded $procmon @('/Terminate','/Quiet') 10 } catch { }
    }
    if (Test-Path -LiteralPath $private) {
        for ($attempt=0; $attempt -lt 10 -and (Test-Path -LiteralPath $private); $attempt++) {
            try { Remove-Item -LiteralPath $private -Recurse -Force } catch { Start-Sleep -Milliseconds 200 }
        }
    }
    $outcome.raw_files_removed = -not (Test-Path -LiteralPath $private)
    $outcome.raw_content_retained = -not $outcome.raw_files_removed
    $outcome | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $report -Encoding utf8
    if (-not $outcome.raw_files_removed) { throw 'PRIVATE_CAPTURE_CLEANUP_FAILED' }
}
