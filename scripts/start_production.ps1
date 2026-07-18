Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$requiredVars = @(
    "SECRET_KEY",
    "MAGAYISA_ADMIN_EMAIL",
    "MAGAYISA_ADMIN_PASSWORD",
    "MAGAYISA_POSTGRES_DSN",
    "MAGAYISA_REDIS_URL",
    "MAGAYISA_PUBLIC_BASE_URL"
)

$missing = @()
foreach ($name in $requiredVars) {
    $value = [Environment]::GetEnvironmentVariable($name)
    if ([string]::IsNullOrWhiteSpace($value)) {
        $missing += $name
    }
}

if ($missing.Count -gt 0) {
    throw "Missing required production variables: $($missing -join ', ')"
}

$env:FLASK_ENV = "production"
$env:MAGAYISA_PRODUCTION = "1"
$env:MAGAYISA_DEBUG = "0"
$env:MAGAYISA_TRUST_PROXY = "1"
$env:MAGAYISA_PROXY_X_FOR = "1"
$env:MAGAYISA_PROXY_X_PROTO = "1"
$env:MAGAYISA_PROXY_X_HOST = "1"
$env:MAGAYISA_PROXY_X_PORT = "1"
$env:MAGAYISA_SESSION_COOKIE_SECURE = "1"
$env:MAGAYISA_SESSION_SAMESITE = "Lax"

if ($env:PAYFAST_SANDBOX -eq "1") {
    throw "PAYFAST_SANDBOX=1 is not allowed in production profile. Set PAYFAST_SANDBOX=0."
}

$port = if ([string]::IsNullOrWhiteSpace($env:PORT)) { "8000" } else { $env:PORT }
$workers = if ([string]::IsNullOrWhiteSpace($env:WEB_CONCURRENCY)) { "4" } else { $env:WEB_CONCURRENCY }

Write-Host "Starting Magayisa in production mode..." -ForegroundColor Cyan
Write-Host "  Bind: 0.0.0.0:$port" -ForegroundColor DarkGray
Write-Host "  Workers: $workers" -ForegroundColor DarkGray
Write-Host "  DB backend: postgres (required)" -ForegroundColor DarkGray
Write-Host "  Redis: enabled (required)" -ForegroundColor DarkGray
Write-Host "  Cookie secure: 1" -ForegroundColor DarkGray

python scripts/production_preflight.py

python -m gunicorn --workers $workers --bind "0.0.0.0:$port" --access-logfile - --error-logfile - app:app
