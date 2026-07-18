Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

# Local-safe defaults for beta testing on this machine.
if (-not $env:SECRET_KEY) {
	$env:SECRET_KEY = [Convert]::ToBase64String((1..48 | ForEach-Object { Get-Random -Maximum 256 }))
}
$env:FLASK_ENV = "production"
$env:MAGAYISA_PRODUCTION = "1"
$env:MAGAYISA_DEBUG = "0"
$env:MAGAYISA_TRUST_PROXY = "1"
$env:MAGAYISA_PROXY_X_FOR = "1"
$env:MAGAYISA_PROXY_X_PROTO = "1"
$env:MAGAYISA_PROXY_X_HOST = "1"
$env:MAGAYISA_PROXY_X_PORT = "1"

# For local HTTP testing, keep secure cookies off. Turn this to 1 behind HTTPS.
$env:MAGAYISA_SESSION_COOKIE_SECURE = "0"
$env:MAGAYISA_SESSION_SAMESITE = "Lax"

$env:MAGAYISA_DATABASE_PATH = Join-Path $repoRoot "instance\golimpopo.sqlite"
$env:MAGAYISA_POSTGRES_DSN = ""
$env:MAGAYISA_REDIS_URL = ""

$env:MAGAYISA_BETA_MODE = "1"
$env:MAGAYISA_BETA_MAX_DAILY_ACTIVE_USERS = "200"
$env:MAGAYISA_BETA_ACTIVITY_WINDOW_SECONDS = "86400"

# PayFast sandbox defaults (replace with your own merchant credentials for real pilot/live use).
if (-not $env:PAYFAST_MERCHANT_ID) { $env:PAYFAST_MERCHANT_ID = "10000100" }
if (-not $env:PAYFAST_MERCHANT_KEY) { $env:PAYFAST_MERCHANT_KEY = "46f0cd694581a" }
if (-not $env:PAYFAST_PASSPHRASE) { $env:PAYFAST_PASSPHRASE = "" }
$env:PAYFAST_SANDBOX = "1"
$env:PAYFAST_TEST_MODE_FALLBACK = "1"

# Set this to your active Cloudflare tunnel/public domain for PayFast callbacks.
if (-not $env:MAGAYISA_PUBLIC_BASE_URL) {
	$env:MAGAYISA_PUBLIC_BASE_URL = "https://adjustment-vitamin-functionality-openings.trycloudflare.com"
}

if (-not $env:MAGAYISA_ADMIN_EMAIL) { $env:MAGAYISA_ADMIN_EMAIL = "admin@magayisa.local" }
if (-not $env:MAGAYISA_ADMIN_PASSWORD) { $env:MAGAYISA_ADMIN_PASSWORD = "Admin123!" }

Write-Host "Starting Magayisa in local public-beta mode..." -ForegroundColor Cyan
Write-Host "  URL: http://127.0.0.1:5000" -ForegroundColor DarkGray
Write-Host "  LAN URL: http://172.20.10.13:5000" -ForegroundColor DarkGray
Write-Host "  Public base URL: $($env:MAGAYISA_PUBLIC_BASE_URL)" -ForegroundColor DarkGray
Write-Host "  Beta cap: $($env:MAGAYISA_BETA_MAX_DAILY_ACTIVE_USERS) active users/day" -ForegroundColor DarkGray
Write-Host "  DB: $($env:MAGAYISA_DATABASE_PATH)" -ForegroundColor DarkGray

python -c "from app import app; app.run(host='0.0.0.0', port=5000, debug=app.config['DEBUG'])"
