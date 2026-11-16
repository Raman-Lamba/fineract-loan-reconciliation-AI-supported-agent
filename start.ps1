# One command to (re)start everything:   .\start.ps1
# Safe to re-run. Add -Seed to load the synthetic borrowers and loans as well.
# Add -Reconcile to build the bank statement, run the matcher and print its accuracy report.
param([switch]$Seed, [switch]$Reconcile)

$root = $PSScriptRoot
$repo = Join-Path $root 'vendor\mcp-mifosx'
$sandbox = Join-Path $repo 'agentic-mifos-sandbox'
$commit = 'be806dbd36daf510dcd787d911dfeab4471a1a8c'   # openMF/mcp-mifosx main, 16 Nov 2026
$bash = 'C:\Program Files\Git\bin\bash.exe'

function Stop-If($failed, $message) { if ($failed) { Write-Host "STOP: $message" -ForegroundColor Red; exit 1 } }

Stop-If (-not (Get-Command docker -ErrorAction SilentlyContinue)) 'Docker is not installed. Install Docker Desktop (WSL 2 backend), reboot, and run this again.'
docker info --format '{{.ServerVersion}}' | Out-Null
Stop-If ($LASTEXITCODE -ne 0) 'Docker is installed but not running. Start Docker Desktop and run this again.'

if (-not (Test-Path $repo)) {
    # autocrlf off: the sandbox mounts shell scripts into Linux containers, which CRLF breaks
    git -c core.autocrlf=false clone https://github.com/openMF/mcp-mifosx.git $repo
    Stop-If ($LASTEXITCODE -ne 0) 'Could not clone the connector.'
    git -C $repo -c core.autocrlf=false checkout --quiet $commit
    Stop-If ($LASTEXITCODE -ne 0) "Could not check out $commit."
}

if (-not (Test-Path (Join-Path $sandbox '.env'))) {
    Push-Location $sandbox; & $bash scripts/gen-secrets.sh; $rc = $LASTEXITCODE; Pop-Location
    Stop-If ($rc -ne 0) 'gen-secrets.sh failed (it needs bash and openssl, both shipped with Git for Windows).'
}

Push-Location $sandbox; docker compose up -d --build --wait; $rc = $LASTEXITCODE; Pop-Location
Stop-If ($rc -ne 0) 'The sandbox did not become healthy. See: docker compose ps (in vendor\mcp-mifosx\agentic-mifos-sandbox).'

$envFile = Join-Path $root '.env'
if (-not (Test-Path $envFile)) {
    # Copy the generated admin password across without printing it.
    $line = Select-String -Path (Join-Path $sandbox '.env') -Pattern '^FINERACT_ADMIN_PASSWORD=(.*)$'
    Stop-If (-not $line) 'FINERACT_ADMIN_PASSWORD is missing from the sandbox .env.'
    [IO.File]::WriteAllLines($envFile, @(
        'MIFOSX_BASE_URL=http://localhost:4200/fineract-provider/api/v1',
        'MIFOSX_TENANT_ID=default',
        'MIFOSX_USERNAME=mifos',
        "MIFOSX_PASSWORD=$($line.Matches[0].Groups[1].Value)"))
}

$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    python -m venv (Join-Path $root '.venv')
    & $py -m pip install --quiet -r (Join-Path $repo 'python\requirements.txt')
    Stop-If ($LASTEXITCODE -ne 0) 'Could not install the connector requirements.'
}

Push-Location $root
& $py scripts\check_connection.py
if ($Seed -and $LASTEXITCODE -eq 0) { & $py -m seed.load_fineract }
if ($Reconcile) {
    foreach ($step in 'statement.fetch_schedules', 'statement.generate', 'matcher.run', 'matcher.evaluate') {
        if ($LASTEXITCODE -eq 0) { & $py -m $step }
    }
}
Pop-Location
