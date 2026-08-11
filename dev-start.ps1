<#
.SYNOPSIS
    Build and run the RecoveryHub Dashboard stack locally in Docker Desktop.

.DESCRIPTION
    Builds the backend and frontend Docker images and starts three containers:
    MongoDB, the FastAPI backend, and the nginx frontend. The frontend nginx
    container proxies /api/* to the backend container via a shared docker
    network.

    The script reads VITE_AZURE_CLIENT_ID and VITE_AZURE_TENANT_ID from the
    local .env file to inject into the frontend build args.

    MongoDB uses a named volume (rhdashboard_mongo-data) so saved dashboards
    persist across restarts.

.PARAMETER Restart
    Skip the image build and just stop + start the existing containers.
    Use this for quick restarts when no code has changed.

.PARAMETER Build
    (Default) Rebuild both images and restart the containers.

.PARAMETER NoCache
    Rebuild both images with --no-cache (full clean build) and restart the
    containers. Use this when Docker's layer cache is stale or you want to
    guarantee a from-scratch build.

.PARAMETER Stop
    Stop and remove the dev containers without rebuilding or starting them.

.EXAMPLE
    .\dev-start.ps1                 # Build + run (default)
    .\dev-start.ps1 -Restart        # Just restart existing containers
    .\dev-start.ps1 -Build          # Rebuild images + restart
    .\dev-start.ps1 -NoCache        # Full clean rebuild + restart
    .\dev-start.ps1 -Build -NoCache # Full clean rebuild + restart
    .\dev-start.ps1 -Stop           # Stop and remove containers
#>
[CmdletBinding(DefaultParameterSetName = 'Build')]
param(
    [Parameter(ParameterSetName = 'Restart')]
    [switch]$Restart,

    [Parameter(ParameterSetName = 'Build')]
    [switch]$Build,

    [Parameter(ParameterSetName = 'Build')]
    [switch]$NoCache,

    [Parameter(ParameterSetName = 'Stop')]
    [switch]$Stop
)

$ErrorActionPreference = 'Stop'
$RepoRoot = $PSScriptRoot
$BackendDir  = Join-Path $RepoRoot 'backend'
$FrontendDir = Join-Path $RepoRoot 'frontend'
$EnvFile     = Join-Path $RepoRoot '.env'

$BackendImage  = 'rh-dashboard-backend:dev'
$FrontendImage = 'rh-dashboard-frontend:dev'
$MongoImage    = 'mongo:6.0'

$BackendContainer  = 'rh-dashboard-backend-dev'
$FrontendContainer = 'rh-dashboard-frontend-dev'
$MongoContainer    = 'rh-dashboard-mongo-dev'
$NetworkName = 'rh-dashboard-dev'
$MongoVolume = 'rhdashboard_mongo-data'

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

function Write-Section([string]$msg) {
    Write-Host "`n=== $msg ===" -ForegroundColor Cyan
}

function Test-DockerReady {
    # Verifies the Docker engine is actually responsive before we issue any
    # docker commands. Without this, a half-started Docker Desktop (UI up but
    # engine WSL distro stopped) makes every `docker` call block forever and
    # the script appears to hang on "Stopping existing dev containers".
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

function Test-ContainerExists([string]$name) {
    # Returns true if a container (running or stopped) with the given name exists.
    $found = docker ps -a --filter "name=^/${name}$" --format '{{.Names}}' 2>$null
    return [bool]$found
}

function Stop-Containers {
    Write-Section 'Stopping existing dev containers'
    foreach ($c in @($FrontendContainer, $BackendContainer, $MongoContainer)) {
        if (Test-ContainerExists $c) {
            docker rm -f $c | Out-Null
            Write-Host "Removed $c"
        } else {
            Write-Host "Skipped $c (not present)"
        }
    }
    Write-Host 'Done.'
}

function Remove-Network {
    $exists = docker network ls --filter "name=$NetworkName" --format '{{.Name}}' 2>$null
    if ($exists) {
        docker network rm $NetworkName | Out-Null
        Write-Host "Removed network: $NetworkName"
    }
}

function New-Network {
    $exists = docker network ls --filter "name=$NetworkName" --format '{{.Name}}' 2>$null
    if (-not $exists) {
        docker network create $NetworkName | Out-Null
        Write-Host "Created network: $NetworkName"
    }
}

function New-Volume {
    $exists = docker volume ls --filter "name=$MongoVolume" --format '{{.Name}}' 2>$null
    if (-not $exists) {
        docker volume create $MongoVolume | Out-Null
        Write-Host "Created volume: $MongoVolume"
    }
}

function Read-EnvVar([string]$key) {
    if (-not (Test-Path $EnvFile)) {
        throw "Missing .env file at $EnvFile"
    }
    $line = Get-Content $EnvFile | Where-Object { $_ -match "^$key=" } | Select-Object -First 1
    if (-not $line) {
        throw "Missing '$key' in $EnvFile"
    }
    return ($line -split '=', 2)[1].Trim()
}

function Build-Images([bool]$noCache) {
    $clientId  = Read-EnvVar 'VITE_AZURE_CLIENT_ID'
    $tenantId  = Read-EnvVar 'VITE_AZURE_TENANT_ID'

    Write-Section 'Building backend image'
    $buildArgs = @('build', '-t', $BackendImage)
    if ($noCache) { $buildArgs += '--no-cache' }
    $buildArgs += $BackendDir
    & docker @buildArgs
    if ($LASTEXITCODE -ne 0) { throw 'Backend image build failed.' }

    Write-Section 'Building frontend image'
    $buildArgs = @(
        'build',
        '-t', $FrontendImage,
        '--build-arg', "VITE_AZURE_CLIENT_ID=$clientId",
        '--build-arg', "VITE_AZURE_TENANT_ID=$tenantId",
        '--build-arg', 'VITE_DEV_AUTH_BYPASS=true'
    )
    if ($noCache) { $buildArgs += '--no-cache' }
    $buildArgs += $FrontendDir
    & docker @buildArgs
    if ($LASTEXITCODE -ne 0) { throw 'Frontend image build failed.' }
}

function Start-Containers {
    Write-Section 'Starting dev containers'

    New-Network
    New-Volume

    # MongoDB: stores saved dashboards and billing data. Uses the persistent
    # volume so dashboards survive container rebuilds/restarts.
    docker run -d `
        --name $MongoContainer `
        --network $NetworkName `
        --network-alias mongo `
        -p 27017:27017 `
        -v "${MongoVolume}:/data/db" `
        $MongoImage
    if ($LASTEXITCODE -ne 0) { throw 'Failed to start MongoDB container.' }

    Write-Host 'Waiting for MongoDB to accept connections...'
    $maxWait = 30
    $waited = 0
    while ($waited -lt $maxWait) {
        $ok = docker exec $MongoContainer mongosh --quiet --eval 'db.runCommand({ping:1}).ok' 2>$null
        if ($ok -match '1') { break }
        Start-Sleep -Seconds 1
        $waited++
    }
    if ($waited -ge $maxWait) {
        Write-Warning 'MongoDB did not respond within 30s — backend may fail to connect.'
    } else {
        Write-Host "MongoDB ready (waited ${waited}s)."
    }

    # Backend: runs on 8001, uses the repo .env for all Azure/Mongo/AI secrets.
    # MONGODB_URI is rewritten from localhost/127.0.0.1 to the mongo container
    # alias for local dev, but kept as-is when pointed at a real cluster.
    $MongodbUri = ''
    foreach ($line in Get-Content $EnvFile) {
        if ($line -match '^MONGODB_URI\s*=\s*(.*)$') {
            $MongodbUri = $Matches[1]
            break
        }
    }
    if ($MongodbUri -match 'localhost|127\.0\.0\.1') {
        $MongodbUri = $MongodbUri -replace 'localhost|127\.0\.0\.1', 'mongo'
    }
    docker run -d `
        --name $BackendContainer `
        --network $NetworkName `
        --network-alias backend `
        -p 8001:8001 `
        --env-file $EnvFile `
        -e "MONGODB_URI=$MongodbUri" `
        $BackendImage
    if ($LASTEXITCODE -ne 0) { throw 'Failed to start backend container.' }

    # Frontend: nginx on port 80 -> mapped to 3000 on host.
    # BACKEND_URL points to the backend container via the shared network.
    docker run -d `
        --name $FrontendContainer `
        --network $NetworkName `
        -p 3000:80 `
        -e 'BACKEND_URL=http://backend:8001' `
        $FrontendImage
    if ($LASTEXITCODE -ne 0) { throw 'Failed to start frontend container.' }

    Write-Host "`n--- Dev stack running ---" -ForegroundColor Green
    Write-Host "Frontend:  http://localhost:3000" -ForegroundColor Yellow
    Write-Host "Backend:   http://localhost:8001/docs" -ForegroundColor Yellow
    Write-Host "MongoDB:   localhost:27017" -ForegroundColor Yellow
    Write-Host "`nLogs:"
    Write-Host "  docker logs -f $MongoContainer"
    Write-Host "  docker logs -f $BackendContainer"
    Write-Host "  docker logs -f $FrontendContainer"
    Write-Host "`nStop:"
    Write-Host "  .\dev-start.ps1 -Stop"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# Fail fast if the Docker engine isn't actually up. Without this, a
# half-started Docker Desktop makes every docker call block forever and
# the script appears to hang on "Stopping existing dev containers".
Test-DockerReady

if ($Stop) {
    Stop-Containers
    Remove-Network
    return
}

if ($Restart) {
    Stop-Containers
    Start-Containers
    return
}

# -Build (default) and -NoCache both rebuild
Stop-Containers
Build-Images -noCache:$NoCache
Start-Containers
