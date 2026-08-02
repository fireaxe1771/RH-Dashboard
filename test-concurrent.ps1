$d = Invoke-RestMethod -Uri http://localhost:8001/api/dashboards -Method Get
$claims = $d | Where-Object { $_.name -like "*Claims*" }
$filters = @{
    department_id = $null
    processor_id  = $null
    start_date    = $null
    end_date      = $null
    range_type    = "week"
    periods_back  = 1
}

$scriptBlock = {
    param($body)
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $null = Invoke-RestMethod -Uri http://localhost:8001/api/query/sql -Method Post -Body $body -ContentType "application/json"
        $sw.Stop()
        "$($sw.ElapsedMilliseconds)ms OK"
    } catch {
        $sw.Stop()
        "$($sw.ElapsedMilliseconds)ms ERR: $($_.Exception.Message)"
    }
}

$jobs = @()
foreach ($w in $claims.widgets) {
    $body = @{ sql_query = $w.sql_query; filters = $filters } | ConvertTo-Json -Depth 5 -Compress
    $jobs += Start-Job -ScriptBlock $scriptBlock -ArgumentList $body
}

$jobs | Wait-Job -Timeout 30 | ForEach-Object {
    $name = $claims.widgets[[array]::IndexOf($jobs, $_)].id
    Write-Output ("{0,-42} {1}" -f $name, (Receive-Job $_))
}
$jobs | Remove-Job
