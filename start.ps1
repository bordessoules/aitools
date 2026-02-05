# MCP Gateway (Lean) - Startup Script
# Requires: Docker services from searxng-mcp, LM Studio

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  MCP Gateway (Lean) - Startup Check" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# Check Docker services
Write-Host "`n[1/3] Checking Docker services..." -ForegroundColor Yellow

$services = @(
    @{Name="mcp-searxng"; Port=8080},
    @{Name="mcp-docling-gpu"; Port=8002},
    @{Name="mcp-docling-cpu"; Port=8001}
)

$allRunning = $true
foreach ($svc in $services) {
    $container = docker ps --filter "name=$($svc.Name)" --format "{{.Names}}" 2>$null
    if ($container -eq $svc.Name) {
        Write-Host "  OK $($svc.Name) :$($svc.Port)" -ForegroundColor Green
    } else {
        Write-Host "  MISSING $($svc.Name)" -ForegroundColor Red
        $allRunning = $false
    }
}

if (-not $allRunning) {
    Write-Host "`n[!] Start services first:" -ForegroundColor Red
    Write-Host "    cd ../searxng-mcp; docker compose up -d" -ForegroundColor Gray
    exit 1
}

# Check LM Studio
Write-Host "`n[2/3] Checking LM Studio..." -ForegroundColor Yellow
try {
    $resp = Invoke-RestMethod -Uri "http://master.tail5bb17d.ts.net:1234/v1/models" -Method GET -TimeoutSec 5
    $model = $resp.data[0].id
    Write-Host "  OK LM Studio responding" -ForegroundColor Green
    Write-Host "    Model: $model" -ForegroundColor DarkGray
} catch {
    Write-Host "  FAIL LM Studio not responding" -ForegroundColor Red
    Write-Host "    Ensure LM Studio is running with API enabled" -ForegroundColor Yellow
}

# Load config
Write-Host "`n[3/3] Configuration..." -ForegroundColor Yellow
python -c "
import sys
sys.path.insert(0, 'src')
import routing
print(f'  SearXNG:    {routing.SEARXNG_URL}')
print(f'  Docling:    {routing.DOCLING_GPU_URL if routing.USE_DOCLING_GPU else routing.DOCLING_URL}')
print(f'  LM Studio:  {routing.LMSTUDIO_URL}')
print(f'  Cache:      {routing.CACHE_DIR}')
"

# Quick test
Write-Host "`n[Test] Search check..." -ForegroundColor Yellow
$searchTest = python -c "import sys; sys.path.insert(0,'src'); import asyncio,routing; r=asyncio.run(routing.search('test',1)); print('OK' if 'Search' in r else 'FAIL')" 2>$null
if ($searchTest -eq "OK") {
    Write-Host "  OK Search working" -ForegroundColor Green
} else {
    Write-Host "  FAIL Search failed" -ForegroundColor Red
}

# Start gateway
Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  Starting MCP Gateway on port 8000" -ForegroundColor Cyan
Write-Host "  Press Ctrl+C to stop" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan

python -m src.gateway -t sse -p 8000
