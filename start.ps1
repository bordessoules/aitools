# MCP Gateway Local Startup Script
# 
# IMPORTANT: For Docker deployment, use deploy.ps1 instead:
#   .\deploy.ps1              # Auto-detect best profile
#   .\deploy.ps1 -Profile gpu # Use GPU profile
#
# This script is for LOCAL development only (running gateway directly).

param(
    [switch]$SkipChecks,
    [switch]$Help,
    
    [ValidateSet("sse", "stdio")]
    [string]$Transport = "sse",
    
    [int]$Port = 8000
)

if ($Help) {
    Write-Host @"
MCP Gateway Local Startup Script

USAGE:
    .\start.ps1 [OPTIONS]

OPTIONS:
    -Transport <sse|stdio>  Transport type: sse (default) or stdio
    -Port <number>          Port for SSE transport (default: 8000)
    -SkipChecks             Skip service health checks
    -Help                   Show this help message

TRANSPORT MODES:
    sse       - HTTP Server-Sent Events (for web clients, multiple clients)
    stdio     - Standard input/output (for Kimi CLI, Cursor, Claude Desktop)

EXAMPLES:
    .\start.ps1                    # Start with SSE on port 8000
    .\start.ps1 -Transport stdio   # Start with stdio for Kimi CLI
    .\start.ps1 -Port 9000         # Start SSE on port 9000

IMPORTANT:
    This script runs the gateway LOCALLY (not in Docker).
    For Docker deployment with profiles, use .\deploy.ps1 instead.

RECIPE B (Local Chrome + stdio for Kimi CLI):
    1. Start Docker services: docker compose --profile gpu up -d
    2. Start gateway: .\start.ps1 -Transport stdio
    3. Configure Kimi CLI with stdio transport
"@ -ForegroundColor White
    exit 0
}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  MCP Gateway - Local Development" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "For Docker deployment, use: .\deploy.ps1" -ForegroundColor Yellow
Write-Host ""

# Load .env file
$envFile = ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match "^([^#][^=]*)=(.*)$") {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2])
        }
    }
    Write-Host "Loaded configuration from .env" -ForegroundColor DarkGray
} else {
    Write-Host "WARNING: .env file not found. Copy from .env.example" -ForegroundColor Yellow
}

# Get config values
$searxngUrl = $env:SEARXNG_URL
$visionApiUrl = $env:VISION_API_URL
$doclingGpuUrl = $env:DOCLING_GPU_URL
$doclingUrl = $env:DOCLING_URL
$useGpu = $env:USE_DOCLING_GPU -eq "true"
$openSearchUrl = $env:OPENSEARCH_URL

if (-not $SkipChecks) {
    Write-Host "`n[1/4] Checking Services..." -ForegroundColor Yellow
    
    # Check SearXNG
    if ($searxngUrl) {
        try {
            $resp = Invoke-RestMethod -Uri "$searxngUrl/healthz" -TimeoutSec 5 -ErrorAction SilentlyContinue
            Write-Host "  [OK] SearXNG at $searxngUrl" -ForegroundColor Green
        } catch {
            Write-Host "  [WARN] SearXNG not responding" -ForegroundColor Yellow
            Write-Host "         Start with: docker compose --profile minimal up -d searxng" -ForegroundColor Gray
        }
    }
    
    # Check Docling GPU (optional)
    if ($useGpu -and $doclingGpuUrl) {
        try {
            $resp = Invoke-RestMethod -Uri "$doclingGpuUrl/health" -TimeoutSec 3 -ErrorAction SilentlyContinue
            Write-Host "  [OK] Docling GPU at $doclingGpuUrl" -ForegroundColor Green
        } catch {
            Write-Host "  [INFO] Docling GPU not available - will use MarkItDown fallback for PDFs" -ForegroundColor DarkGray
        }
    }
    
    # Check Docling CPU (optional)
    if ($doclingUrl -and -not $useGpu) {
        try {
            $resp = Invoke-RestMethod -Uri "$doclingUrl/health" -TimeoutSec 3 -ErrorAction SilentlyContinue
            Write-Host "  [OK] Docling CPU at $doclingUrl" -ForegroundColor Green
        } catch {
            Write-Host "  [INFO] Docling CPU not available" -ForegroundColor DarkGray
        }
    }

    # Check Vision API (optional)
    if ($visionApiUrl) {
        try {
            $resp = Invoke-RestMethod -Uri "$visionApiUrl/models" -TimeoutSec 5 -ErrorAction SilentlyContinue
            $model = $resp.data[0].id
            Write-Host "  [OK] Vision API at $visionApiUrl" -ForegroundColor Green
            Write-Host "       Model: $model" -ForegroundColor DarkGray
        } catch {
            Write-Host "  [WARN] Vision API not responding at $visionApiUrl" -ForegroundColor Yellow
            Write-Host "         Vision extraction will be disabled" -ForegroundColor Gray
        }
    }
    
    # Check OpenSearch
    if ($openSearchUrl) {
        try {
            $resp = Invoke-RestMethod -Uri "$openSearchUrl/_cluster/health" -TimeoutSec 3 -ErrorAction SilentlyContinue
            Write-Host "  [OK] OpenSearch" -ForegroundColor Green
        } catch {
            Write-Host "  [INFO] OpenSearch not available - knowledge base disabled" -ForegroundColor DarkGray
            Write-Host "         Start with: docker compose --profile standard up -d opensearch" -ForegroundColor Gray
        }
    }
}

Write-Host "`n[2/4] Starting Gateway locally..." -ForegroundColor Yellow

# Check Python environment
$pythonPath = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $pythonPath) {
    Write-Host "[ERROR] Python not found in PATH" -ForegroundColor Red
    exit 1
}

Write-Host "  Using Python: $pythonPath" -ForegroundColor Gray

# Test imports
Write-Host "`n[3/4] Testing imports..." -ForegroundColor Yellow
$testResult = python -c "
import sys
sys.path.insert(0, 'src')
try:
    import config
    print('[OK] Config loaded')
    print(f'  SearXNG: {config.SEARXNG_URL}')
    print(f'  Vision API: {config.VISION_API_URL or \"(not configured)\"}')
    print(f'  Docling: {config.DOCLING_URL}')
    print(f'  Cache: {config.CACHE_DIR}')
except Exception as e:
    print(f'[ERROR] {e}')
    sys.exit(1)
" 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Failed to load configuration" -ForegroundColor Red
    exit 1
}

# Kill any existing gateway on the port
$existing = netstat -ano | Select-String ":$Port\s.*LISTENING"
if ($existing) {
    $existingPid = ($existing.Line -split '\s+')[-1]
    Write-Host "`n[4/4] Killing existing process on port $Port (PID: $existingPid)..." -ForegroundColor Yellow
    Stop-Process -Id $existingPid -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
} else {
    Write-Host "`n[4/4] Starting server..." -ForegroundColor Yellow
}
Write-Host "========================================" -ForegroundColor Cyan

if ($Transport -eq "stdio") {
    Write-Host "  Transport: stdio (for Kimi CLI, Cursor, Claude Desktop)" -ForegroundColor Cyan
    Write-Host "  Docker services must be running separately" -ForegroundColor Yellow
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    python -m src.gateway -t stdio
} else {
    Write-Host "  Transport: SSE" -ForegroundColor Cyan
    Write-Host "  Gateway: http://localhost:$Port" -ForegroundColor Cyan
    Write-Host "  Press Ctrl+C to stop" -ForegroundColor Yellow
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host ""
    python -m src.gateway -t sse -p $Port
}
