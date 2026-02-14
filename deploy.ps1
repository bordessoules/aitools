# MCP Gateway Deployment Script
# Usage: .\deploy.ps1 [-Profile <profile>] [-DryRun] [-Help]
#
# This script helps you deploy MCP Gateway with the right configuration
# for your hardware and needs.

param(
    [ValidateSet("auto", "minimal", "standard", "cpu", "gpu")]
    [string]$Profile = "auto",
    
    [switch]$DryRun,
    [switch]$Help,
    [switch]$SkipEnvCheck
)

# ============================================================================
# CONSTANTS & CONFIGURATION
# ============================================================================

$VERSION = "1.0.0"
$REQUIRED_PORTS = @(8000, 8080, 9200, 5601, 5001)

# Profile definitions with clear use cases
$PROFILES = @{
    minimal = @{
        Name = "Minimal"
        Description = "Text-only extraction, no LLM needed"
        Services = @("gateway", "searxng")
        Requirements = @("Docker only")
        Recommendations = @("4GB RAM", "No GPU needed", "No LLM needed")
        UseCase = "VPS/Cloud: Cheapest option, text extraction only"
        WebExtraction = "MarkItDown (fast, no browser needed)"
        Color = "DarkGray"
    }
    standard = @{
        Name = "Standard"
        Description = "With Knowledge Base, MarkItDown for everything"
        Services = @("gateway", "searxng", "opensearch")
        Requirements = @("Docker", "4GB+ RAM")
        Recommendations = @("8GB RAM recommended", "No GPU needed")
        UseCase = "VPS/Cloud: Persistent storage + search"
        WebExtraction = "MarkItDown (Docker-friendly)"
        Color = "Green"
    }
    cpu = @{
        Name = "CPU-Optimized"
        Description = "Full features with Docling CPU (slower PDFs)"
        Services = @("gateway", "searxng", "opensearch", "docling-cpu")
        Requirements = @("Docker", "8GB+ RAM")
        Recommendations = @("16GB RAM recommended", "CPU-intensive PDF processing")
        UseCase = "Local: Best PDF quality without GPU"
        WebExtraction = "MarkItDown or Chrome Extension (optional)"
        Color = "Cyan"
    }
    gpu = @{
        Name = "GPU-Accelerated"
        Description = "Best performance with Docling GPU"
        Services = @("gateway", "searxng", "opensearch", "docling-gpu")
        Requirements = @("Docker", "NVIDIA GPU", "NVIDIA Container Toolkit")
        Recommendations = @("8GB+ RAM", "NVIDIA GPU with 4GB+ VRAM")
        UseCase = "Local: Maximum performance for PDFs"
        WebExtraction = "MarkItDown or Chrome Extension (optional)"
        Color = "Yellow"
    }
}

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

function Write-Header($text) {
    Write-Host "`n========================================" -ForegroundColor Cyan
    Write-Host "  $text" -ForegroundColor Cyan
    Write-Host "========================================" -ForegroundColor Cyan
}

function Write-Step($number, $text) {
    Write-Host "`n[$number] $text" -ForegroundColor Yellow
}

function Test-Command($command) {
    return [bool](Get-Command -Name $command -ErrorAction SilentlyContinue)
}

function Test-DockerRunning {
    try {
        $null = docker info 2>&1
        return $true
    } catch {
        return $false
    }
}

function Test-GPUAvailable {
    try {
        $nvidia = nvidia-smi 2>&1
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Test-NvidiaContainerToolkit {
    try {
        $runtime = docker info --format '{{json .Runtimes}}' | ConvertFrom-Json -ErrorAction SilentlyContinue
        return $runtime.nvidia -ne $null
    } catch {
        return $false
    }
}

function Get-AvailablePort($port) {
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Any, $port)
        $listener.Start()
        $listener.Stop()
        return $true
    } catch {
        return $false
    }
}

function Show-Help {
    Write-Host @"
MCP Gateway Deployment Script v$VERSION

USAGE:
    .\deploy.ps1 [OPTIONS]

OPTIONS:
    -Profile <name>    Deployment profile: auto, minimal, standard, cpu, gpu (default: auto)
    -DryRun            Show what would be done without doing it
    -SkipEnvCheck      Skip environment checks
    -Help              Show this help message

PROFILES:
    minimal   - Text-only, no LLM needed (~4GB RAM)
    standard  - With Knowledge Base (~8GB RAM)
    cpu       - CPU PDF processing (~16GB RAM)
    gpu       - GPU-accelerated PDFs (~8GB+ RAM, needs NVIDIA GPU)

DEPLOYMENT TIERS (see DEPLOYMENT.md):
    A. GPU + Local LM Studio  - Fast PDFs, local vision
    B. GPU + Cloud API        - Fast PDFs, OpenAI/Together
    C. CPU + Knowledge Base   - Persistent storage
    D. Minimal (Text Only)    - Cheapest, no LLM

WEB EXTRACTION:
    MarkItDown is used for all Docker deployments (fast, works everywhere).
    Chrome Extension is optional for local development only.

EXAMPLES:
    .\deploy.ps1                    # Auto-detect best profile
    .\deploy.ps1 -Profile minimal   # Minimal deployment
    .\deploy.ps1 -Profile gpu       # GPU-accelerated deployment
    .\deploy.ps1 -DryRun            # Preview without deploying

NOTES:
    - Auto-detection considers: GPU, RAM, Vision API availability
    - First run creates .env from .env.example
    - For GPU profile, install NVIDIA Container Toolkit first
    - Granite-258M runs locally in container (no external API needed)
    - Set VISION_API_URL for picture descriptions (optional)
"@ -ForegroundColor White
}

function Show-ChromeExtensionNotice {
    Write-Host "`n========================================" -ForegroundColor Yellow
    Write-Host "  CHROME EXTENSION NOTE" -ForegroundColor Yellow
    Write-Host "========================================" -ForegroundColor Yellow
    Write-Host "The Playwright MCP Chrome Extension CANNOT run in Docker." -ForegroundColor White
    Write-Host ""
    Write-Host "For Docker deployments, we use MarkItDown instead:" -ForegroundColor Gray
    Write-Host "  - Fast (~0.4s per page)" -ForegroundColor Green
    Write-Host "  - Excellent for docs, GitHub, news sites" -ForegroundColor Green
    Write-Host "  - Works inside containers" -ForegroundColor Green
    Write-Host "  - Less accurate on React/Vue SPAs (rare)" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "For Chrome Extension (local dev only):" -ForegroundColor Gray
    Write-Host "  1. Use 'cpu' or 'gpu' profile" -ForegroundColor Gray
    Write-Host "  2. Set PLAYWRIGHT_MCP_TOKEN in .env" -ForegroundColor Gray
    Write-Host "  3. See DEPLOYMENT.md for details" -ForegroundColor Gray
    Write-Host "========================================" -ForegroundColor Yellow
}

function Show-ProfileSelection {
    Write-Header "DEPLOYMENT PROFILE SELECTION"
    
    Write-Host "`nChoose based on where you're deploying:`n" -ForegroundColor White
    
    Write-Host "VPS/CLOUD (Docker only, no Chrome extension):" -ForegroundColor Cyan
    Write-Host "  1. Minimal - Text extraction, no LLM (~4GB)" -ForegroundColor DarkGray
    Write-Host "     Best for: Cheapest VPS, just need web search + text" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  2. Standard - With Knowledge Base (~8GB)" -ForegroundColor Green
    Write-Host "     Best for: Persistent storage, document search" -ForegroundColor Gray
    Write-Host ""
    
    Write-Host "LOCAL MACHINE (Can use Chrome extension):" -ForegroundColor Cyan
    Write-Host "  3. CPU - Full features, slower PDFs (~16GB)" -ForegroundColor Cyan
    Write-Host "     Best for: Best PDF quality without GPU" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  4. GPU - Maximum performance, needs NVIDIA GPU (~8GB)" -ForegroundColor Yellow
    Write-Host "     Best for: Heavy PDF processing with GPU" -ForegroundColor Gray
    Write-Host ""
    
    Write-Host "  0. Cancel deployment" -ForegroundColor Red
    
    Write-Host "`nNote: Chrome Extension only works locally, not in Docker." -ForegroundColor DarkGray
    Write-Host "      For Docker, MarkItDown provides excellent web extraction." -ForegroundColor DarkGray
    
    do {
        $choice = Read-Host "`nSelect profile (0-4)"
    } while ($choice -notmatch '^[0-4]$')
    
    if ($choice -eq "0") {
        exit 0
    }
    
    return ($PROFILES.Keys | Sort-Object)[$choice - 1]
}

function Test-VisionAPIAvailable {
    param([string]$url)
    if (-not $url) { return $false }
    try {
        $resp = Invoke-RestMethod -Uri "$url/models" -TimeoutSec 3 -ErrorAction SilentlyContinue
        return $true
    } catch {
        return $false
    }
}

function Invoke-AutoDetect {
    Write-Step "Auto" "Detecting best deployment profile..."

    $hasGPU = Test-GPUAvailable
    $hasNvidiaToolkit = Test-NvidiaContainerToolkit
    $hasVisionAPI = Test-VisionAPIAvailable -url $env:VISION_API_URL

    # Get total RAM in GB (approximate)
    $ramGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)

    Write-Host "  System: $ramGB GB RAM" -ForegroundColor Gray
    Write-Host "  GPU: $(if ($hasGPU) { 'Detected' } else { 'Not detected' })" -ForegroundColor Gray
    Write-Host "  NVIDIA Container Toolkit: $(if ($hasNvidiaToolkit) { 'Installed' } else { 'Not installed' })" -ForegroundColor Gray
    Write-Host "  Vision API: $(if ($hasVisionAPI) { 'Available (picture descriptions enabled)' } else { 'Not detected (optional)' })" -ForegroundColor Gray

    # Decision logic - Granite-258M always runs locally, Vision API is optional
    if ($hasGPU -and $hasNvidiaToolkit -and $ramGB -ge 8) {
        Write-Host "`n  => Recommended: GPU profile (fastest document processing)" -ForegroundColor Green
        if ($hasVisionAPI) {
            Write-Host "     Granite-258M (local) + Vision API for picture descriptions" -ForegroundColor Gray
        } else {
            Write-Host "     Granite-258M (local), no picture descriptions" -ForegroundColor Gray
        }
        return "gpu"
    } elseif ($ramGB -ge 8) {
        Write-Host "`n  => Recommended: CPU profile (full features, no GPU needed)" -ForegroundColor Green
        Write-Host "     Granite-258M runs locally in container" -ForegroundColor Gray
        return "cpu"
    } elseif ($ramGB -ge 4) {
        Write-Host "`n  => Recommended: Standard profile (knowledge base support)" -ForegroundColor Green
        return "standard"
    } else {
        Write-Host "`n  => Recommended: Minimal profile (lightest setup)" -ForegroundColor Green
        Write-Host "     Text extraction works without any VLM." -ForegroundColor Gray
        return "minimal"
    }
}

function Test-Prerequisites($profile) {
    Write-Step "Check" "Checking prerequisites..."
    
    $checks = @()
    
    # Check Docker
    $hasDocker = Test-Command "docker"
    $checks += [PSCustomObject]@{ Component = "Docker"; Required = $true; Status = $hasDocker }
    
    if ($hasDocker) {
        $dockerRunning = Test-DockerRunning
        $checks += [PSCustomObject]@{ Component = "Docker Daemon"; Required = $true; Status = $dockerRunning }
        
        # Check docker compose
        $hasCompose = (docker compose version) -match "Docker Compose"
        $checks += [PSCustomObject]@{ Component = "Docker Compose"; Required = $true; Status = $hasCompose }
    }
    
    # Profile-specific checks
    if ($profile -eq "gpu") {
        $hasGPU = Test-GPUAvailable
        $hasNvidiaToolkit = Test-NvidiaContainerToolkit
        $checks += [PSCustomObject]@{ Component = "NVIDIA GPU"; Required = $true; Status = $hasGPU }
        $checks += [PSCustomObject]@{ Component = "NVIDIA Container Toolkit"; Required = $true; Status = $hasNvidiaToolkit }
        
        if (-not $hasNvidiaToolkit) {
            Write-Host "`n  WARNING: NVIDIA Container Toolkit not installed!" -ForegroundColor Red
            Write-Host "  Install from: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html" -ForegroundColor Yellow
        }
    }
    
    # Display results
    $allPassed = $true
    foreach ($check in $checks) {
        $statusColor = if ($check.Status) { "Green" } else { if ($check.Required) { "Red" } else { "Yellow" } }
        $statusText = if ($check.Status) { "OK" } else { if ($check.Required) { "MISSING" } else { "OPTIONAL" } }
        Write-Host "  [$statusText] $($check.Component)" -ForegroundColor $statusColor
        if ($check.Required -and -not $check.Status) { $allPassed = $false }
    }
    
    return $allPassed
}

function Test-EnvFile {
    if (-not (Test-Path ".env")) {
        Write-Step "Config" "Creating .env file..."
        
        if (Test-Path ".env.example") {
            Copy-Item ".env.example" ".env"
            Write-Host "  Created .env from .env.example" -ForegroundColor Green
            Write-Host "  Please review and customize it before starting services" -ForegroundColor Yellow
            return $false
        } else {
            Write-Host "  WARNING: .env.example not found!" -ForegroundColor Red
            return $false
        }
    }
    return $true
}

function Invoke-Deployment($profile) {
    # Show Chrome extension notice for local profiles
    if ($profile -in @("cpu", "gpu")) {
        Show-ChromeExtensionNotice
    }
    
    Write-Header "DEPLOYING MCP GATEWAY"
    
    $p = $PROFILES[$profile]
    Write-Host "Profile: " -NoNewline
    Write-Host "$($p.Name)" -ForegroundColor $p.Color
    Write-Host "Services: $($p.Services -join ', ')"
    Write-Host "Web Extraction: $($p.WebExtraction)" -ForegroundColor Gray
    
    if ($DryRun) {
        Write-Host "`n[DRY RUN] Would execute:" -ForegroundColor Magenta
        Write-Host "  docker compose --profile $profile up -d" -ForegroundColor Gray
        return
    }
    
    # Check port availability
    Write-Host "`nChecking port availability..." -ForegroundColor Gray
    $portChecks = @(
        @{ Port = 8000; Service = "Gateway" }
        @{ Port = 8080; Service = "SearXNG" }
        @{ Port = 9200; Service = "OpenSearch"; Profiles = @("standard", "cpu", "gpu") }
        @{ Port = 5601; Service = "Dashboards"; Profiles = @("standard", "cpu", "gpu") }
        @{ Port = 5001; Service = "Docling CPU"; Profiles = @("cpu") }
        @{ Port = 5001; Service = "Docling GPU"; Profiles = @("gpu") }
    )
    
    foreach ($check in $portChecks) {
        if ($check.Profiles -and $profile -notin $check.Profiles) { continue }
        
        $available = Get-AvailablePort $check.Port
        if (-not $available) {
            Write-Host "  [WARN] Port $($check.Port) is in use ($($check.Service))" -ForegroundColor Yellow
        } else {
            Write-Host "  [OK] Port $($check.Port) available ($($check.Service))" -ForegroundColor DarkGray
        }
    }
    
    # Pull images first
    Write-Host "`nPulling Docker images..." -ForegroundColor Yellow
    docker compose --profile $profile pull
    
    # Start services
    Write-Host "`nStarting services..." -ForegroundColor Yellow
    docker compose --profile $profile up -d
    
    if ($LASTEXITCODE -ne 0) {
        Write-Host "`n[ERROR] Deployment failed!" -ForegroundColor Red
        exit 1
    }
    
    # Wait for health checks
    Write-Host "`nWaiting for services to be healthy..." -ForegroundColor Yellow
    Start-Sleep -Seconds 5
    
    # Show status
    Write-Host "`nService status:" -ForegroundColor Gray
    docker compose --profile $profile ps
    
    # Show access URLs
    Write-Header "DEPLOYMENT COMPLETE"
    Write-Host "Services are starting up. Access URLs:" -ForegroundColor White
    Write-Host "  Gateway:    http://localhost:8000" -ForegroundColor Cyan
    Write-Host "  SearXNG:    http://localhost:8080" -ForegroundColor Cyan
    
    if ($profile -in @("standard", "cpu", "gpu")) {
        Write-Host "  OpenSearch: http://localhost:9200" -ForegroundColor Cyan
        Write-Host "  Dashboards: http://localhost:5601" -ForegroundColor Cyan
    }
    
    Write-Host "`nUseful commands:" -ForegroundColor Gray
    Write-Host "  View logs:    docker compose --profile $profile logs -f" -ForegroundColor DarkGray
    Write-Host "  Stop:         docker compose --profile $profile down" -ForegroundColor DarkGray
    Write-Host "  Restart:      docker compose --profile $profile restart" -ForegroundColor DarkGray
}

# ============================================================================
# MAIN EXECUTION
# ============================================================================

if ($Help) {
    Show-Help
    exit 0
}

Write-Header "MCP Gateway Deployment v$VERSION"

# Check if we should auto-detect
if ($Profile -eq "auto") {
    $Profile = Invoke-AutoDetect
    
    $proceed = Read-Host "`nProceed with '$Profile' profile? (Y/n/change)"
    if ($proceed -eq "change" -or $proceed -eq "c") {
        $Profile = Show-ProfileSelection
    } elseif ($proceed -eq "n") {
        exit 0
    }
} else {
    # Validate user-specified profile
    if (-not $PROFILES.ContainsKey($Profile)) {
        Write-Host "[ERROR] Unknown profile: $Profile" -ForegroundColor Red
        Write-Host "Valid profiles: $($PROFILES.Keys -join ', ')" -ForegroundColor Gray
        exit 1
    }
}

# Check prerequisites
if (-not $SkipEnvCheck) {
    $prereqsOk = Test-Prerequisites -profile $Profile
    if (-not $prereqsOk) {
        $continue = Read-Host "`nPrerequisites not met. Continue anyway? (y/N)"
        if ($continue -ne "y") {
            exit 1
        }
    }
}

# Check .env file
$envOk = Test-EnvFile
if (-not $envOk) {
    $editNow = Read-Host "`nEdit .env file now? (Y/n)"
    if ($editNow -ne "n") {
        if (Test-Command "code") {
            code .env
        } elseif (Test-Command "notepad") {
            notepad .env
        }
        Read-Host "Press Enter when ready to continue"
    }
}

# Deploy
Invoke-Deployment -profile $Profile
