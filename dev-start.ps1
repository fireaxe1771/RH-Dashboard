<#
.SYNOPSIS
    Build, run, and monitor the RecoveryHub Dashboard stack locally in Docker
    Desktop.

.DESCRIPTION
    A thin wrapper over `docker compose` for the services defined in
    docker-compose.yml: MongoDB, the FastAPI backend, and the nginx frontend.
    The frontend nginx container proxies /api/* to the backend container over a
    shared docker network.

    Configuration (Azure, Mongo, AI credentials, and the VITE_* build args)
    comes from the local .env file, which Compose reads automatically.

    MongoDB uses a named volume (rhdashboard_mongo-data) so saved dashboards
    persist across restarts.

    NOTE: MONGODB_URI in .env determines which database the backend uses. If it
    points at an Atlas cluster, the local mongo container is unused. Set
    MONGODB_URI=mongodb://mongo:27017 to use the local container instead.

.PARAMETER Build
    (Default) Rebuild both images and restart the containers.

.PARAMETER NoCache
    Rebuild both images with --no-cache (full clean build) and restart the
    containers. Use this when Docker's layer cache is stale or you want to
    guarantee a from-scratch build.

.PARAMETER Restart
    Skip the image build and just restart the existing containers.
    Use this for quick restarts when no code has changed.

.PARAMETER Stop
    Stop and remove the dev containers without rebuilding or starting them.
    The MongoDB volume is preserved.

.PARAMETER Logs
    Follow the logs of all running services and exit without building or
    restarting anything. Equivalent to `docker compose logs -f`.

.PARAMETER Follow
    After starting the stack, follow the logs instead of returning to the
    prompt. Combine with -Build, -NoCache, or -Restart.

.PARAMETER Service
    Limit -Logs / -Follow to specific services (mongo, backend, frontend).
    Defaults to all.

.PARAMETER Tail
    Number of existing log lines to show per service when following.
    Defaults to 50. Accepts a number or 'all'.

.EXAMPLE
    .\dev-start.ps1                        # Build + run (default)
    .\dev-start.ps1 -NoCache               # Full clean rebuild + restart
    .\dev-start.ps1 -Restart               # Just restart existing containers
    .\dev-start.ps1 -Stop                  # Stop and remove containers
    .\dev-start.ps1 -Logs                  # Follow all logs
    .\dev-start.ps1 -Logs -Service backend # Follow backend logs only
    .\dev-start.ps1 -Follow                # Build + run, then follow logs
    .\dev-start.ps1 -Logs -Tail all        # Follow with full history
#>
[CmdletBinding(DefaultParameterSetName = 'Build')]
param(
    [Parameter(ParameterSetName = 'Build')]
    [switch]$Build,

    [Parameter(ParameterSetName = 'Build')]
    [switch]$NoCache,

    [Parameter(ParameterSetName = 'Restart')]
    [switch]$Restart,

    [Parameter(ParameterSetName = 'Stop')]
    [switch]$Stop,

    [Parameter(ParameterSetName = 'Logs', Mandatory = $true)]
    [switch]$Logs,

    [Parameter(ParameterSetName = 'Build')]
    [Parameter(ParameterSetName = 'Restart')]
    [switch]$Follow,

    [Parameter(ParameterSetName = 'Build')]
    [Parameter(ParameterSetName = 'Restart')]
    [Parameter(ParameterSetName = 'Logs')]
    [ValidateSet('mongo', 'backend', 'frontend')]
    [string[]]$Service = @(),

    [Parameter(ParameterSetName = 'Build')]
    [Parameter(ParameterSetName = 'Restart')]
    [Parameter(ParameterSetName = 'Logs')]
    [string]$Tail = '50'
)

$ErrorActionPreference = 'Stop'
$RepoRoot = $PSScriptRoot
$EnvFile  = Join-Path $RepoRoot '.env'

# Every docker call is scoped to this repo's compose file so the script works
# regardless of the caller's working directory.
$Compose = @('compose', '--project-directory', $RepoRoot, '-f', (Join-Path $RepoRoot 'docker-compose.yml'))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Write-Section([string]$msg) {
    Write-Host "`n=== $msg ===" -ForegroundColor Cyan
}

function Invoke-Compose {
    # Runs `docker compose ...` and throws on failure. Output is streamed
    # straight through so build progress stays interactive.
    #
    # Takes an explicit array rather than using ValueFromRemainingArguments:
    # the binder silently DISCARDS single-dash tokens it cannot match to a
    # parameter name, so `Invoke-Compose up -d` dropped the -d and started the
    # stack attached instead of detached.
    param([Parameter(Mandatory = $true)][string[]]$ComposeArgs)

    & docker @Compose @ComposeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose $($ComposeArgs -join ' ') failed with exit code $LASTEXITCODE."
    }
}

function Test-DockerReady {
    # Verifies the Docker engine is actually responsive before we issue any
    # docker commands. Without this, a half-started Docker Desktop (UI up but
    # engine WSL distro stopped) makes every `docker` call block forever and
    # the script appears to hang.
    Write-Section 'Checking Docker engine'
    $job = Start-Job -ScriptBlock {
        $v = docker version --format '{{.Server.Version}}' 2>&1
        # Return exit code AND output so the parent can judge both.
        return [PSCustomObject]@{ ExitCode = $LASTEXITCODE; Output = $v }
    }
    if (Wait-Job $job -Timeout 15) {
        $result = Receive-Job $job
        Remove-Job $job -Force
        $out = if ($result.Output) { $result.Output } else { '' }
        $code = if ($null -ne $result.ExitCode) { $result.ExitCode } else { 1 }
        if ($code -eq 0 -and "$out" -match '\d+\.\d+') {
            Write-Host "Docker engine ready (server $out)." -ForegroundColor Green
            return
        }
        $msg = @"
Docker engine is not responding correctly.

`docker version` returned (exit $code):
$out

Common causes:
  - Docker Desktop is still starting up (wait ~30s and retry)
  - The Docker Desktop Linux engine/WSL distro is stopped or crashed
    (check Docker Desktop UI, or run: wsl --shutdown then restart Docker Desktop)
  - Wrong docker context (run: docker context ls)
"@
        throw $msg
    }
    Remove-Job $job -Force
    throw @"
Docker engine did not respond within 15 seconds. It appears to be hung.

Docker Desktop may be mid-startup or in a broken state. Try:
  1. Quit Docker Desktop fully (right-click tray icon -> Quit)
  2. wsl --shutdown
  3. Start Docker Desktop again and wait for the engine to be ready
  4. Re-run this script
"@
}

function Test-EnvFile {
    # Compose fails with an opaque interpolation error if the VITE_* build args
    # are missing, so check for them up front with an actionable message.
    if (-not (Test-Path $EnvFile)) {
        throw "Missing .env file at $EnvFile"
    }
    $content = Get-Content $EnvFile
    foreach ($key in @('VITE_AZURE_CLIENT_ID', 'VITE_AZURE_TENANT_ID')) {
        $line = $content | Where-Object { $_ -match "^\s*$key\s*=\s*\S" } | Select-Object -First 1
        if (-not $line) {
            throw "Missing '$key' in $EnvFile (required as a frontend build arg)."
        }
    }
}

function Show-Logs {
    # `docker compose logs` can return immediately with no useful context when
    # there are no running containers. Check the Compose-managed services first
    # and give the user an actionable error instead.
    $running = @(& docker @Compose ps --format '{{.Service}}' 2>$null)
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not inspect the Compose stack before following logs.'
    }
    if (-not $running) {
        throw "No RecoveryHub Dashboard containers are running. Start the stack first: .\dev-start.ps1"
    }

    if ($Service.Count -gt 0) {
        $missing = @($Service | Where-Object { $_ -notin $running })
        if ($missing.Count -gt 0) {
            throw "Requested service(s) are not running: $($missing -join ', '). Running services: $($running -join ', ')."
        }
    }

    Write-Section 'Following logs (Ctrl+C to stop; containers keep running)'
    # Not routed through Invoke-Compose: Ctrl+C makes `logs -f` exit non-zero,
    # which is the normal way to stop following and must not throw.
    & docker @Compose logs -f --tail $Tail @Service
}

function Show-Endpoints {
    Write-Host "`n--- Dev stack running ---" -ForegroundColor Green
    Write-Host "Frontend:  http://localhost:3000" -ForegroundColor Yellow
    Write-Host "Backend:   http://localhost:8001/docs" -ForegroundColor Yellow
    Write-Host "MongoDB:   localhost:27017" -ForegroundColor Yellow
    Write-Host "`nLogs:"
    Write-Host "  .\dev-start.ps1 -Logs"
    Write-Host "`nStop:"
    Write-Host "  .\dev-start.ps1 -Stop"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# Fail fast if the Docker engine isn't actually up. Without this, a
# half-started Docker Desktop makes every docker call block forever and
# the script appears to hang.
Test-DockerReady

if ($Logs) {
    Show-Logs
    return
}

if ($Stop) {
    Write-Section 'Stopping dev containers'
    # No -v: the mongo-data volume is preserved so dashboards survive.
    Invoke-Compose @('down', '--remove-orphans')
    Write-Host 'Done.'
    return
}

Test-EnvFile

if (-not $Restart) {
    Write-Section 'Building images'
    $buildArgs = @('build')
    if ($NoCache) { $buildArgs += '--no-cache' }
    Invoke-Compose $buildArgs
}

Write-Section 'Starting dev containers'
# Recreates containers whose config or image changed. --wait blocks until the
# healthchecks that gate depends_on report healthy, so the endpoints printed
# below are actually serving by the time the script returns.
Invoke-Compose @('up', '-d', '--remove-orphans', '--wait')

Show-Endpoints

if ($Follow) { Show-Logs }
