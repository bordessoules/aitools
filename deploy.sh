#!/bin/bash
#
# MCP Gateway Deployment Script
# Usage: ./deploy.sh [OPTIONS]
#
# This script helps you deploy MCP Gateway with the right configuration
# for your hardware and needs.

set -e

VERSION="1.0.0"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
GRAY='\033[0;37m'
MAGENTA='\033[0;35m'
NC='\033[0m' # No Color

# ============================================================================
# CONFIGURATION
# ============================================================================

PROFILE="auto"
DRY_RUN=false
SKIP_ENV_CHECK=false
SHOW_HELP=false

# Profile definitions
declare -A PROFILE_NAMES=(
    ["minimal"]="Minimal"
    ["standard"]="Standard"
    ["cpu"]="CPU-Optimized"
    ["gpu"]="GPU-Accelerated"
)

declare -A PROFILE_DESCRIPTIONS=(
    ["minimal"]="Gateway + Search (NO LLM needed - text extraction only)"
    ["standard"]="+Knowledge Base (no Docling, MarkItDown for docs)"
    ["cpu"]="+Docling CPU (full features, no GPU needed)"
    ["gpu"]="+Docling GPU (best performance, requires NVIDIA GPU)"
)

declare -A PROFILE_COLORS=(
    ["minimal"]="${GRAY}"
    ["standard"]="${GREEN}"
    ["cpu"]="${CYAN}"
    ["gpu"]="${YELLOW}"
)

declare -A PROFILE_RAM=(
    ["minimal"]="4GB, No LLM needed"
    ["standard"]="8GB"
    ["cpu"]="16GB"
    ["gpu"]="8GB + NVIDIA GPU"
)

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

header() {
    echo ""
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}  $1${NC}"
    echo -e "${CYAN}========================================${NC}"
}

step() {
    echo ""
    echo -e "${YELLOW}[$1] $2${NC}"
}

info() {
    echo -e "${GRAY}  $1${NC}"
}

success() {
    echo -e "${GREEN}  [OK] $1${NC}"
}

warning() {
    echo -e "${YELLOW}  [WARN] $1${NC}"
}

error() {
    echo -e "${RED}  [ERROR] $1${NC}"
}

# Check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check if Docker is running
docker_running() {
    docker info >/dev/null 2>&1
}

# Check if NVIDIA GPU is available
gpu_available() {
    if command_exists nvidia-smi; then
        nvidia-smi >/dev/null 2>&1
        return $?
    fi
    return 1
}

# Check if NVIDIA Container Toolkit is installed
nvidia_toolkit_installed() {
    if docker_running; then
        docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q nvidia
        return $?
    fi
    return 1
}

# Check if port is available
port_available() {
    local port=$1
    if command_exists nc; then
        ! nc -z localhost "$port" 2>/dev/null
    else
        # Fallback using /dev/tcp
        ! (echo >/dev/tcp/localhost/"$port") 2>/dev/null
    fi
}

# Get total RAM in GB
get_ram_gb() {
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        free -g | awk '/^Mem:/{print $2}'
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        echo "$(($(sysctl -n hw.memsize) / 1024 / 1024 / 1024))"
    else
        echo "8"  # Default assumption
    fi
}

# Show help
show_help() {
    cat << EOF
MCP Gateway Deployment Script v$VERSION

USAGE:
    ./deploy.sh [OPTIONS]

OPTIONS:
    -p, --profile <name>   Deployment profile: auto, minimal, standard, cpu, gpu (default: auto)
    -d, --dry-run          Show what would be done without doing it
    -s, --skip-env-check   Skip environment checks
    -h, --help             Show this help message

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
    ./deploy.sh                    # Auto-detect best profile
    ./deploy.sh -p minimal         # Minimal deployment
    ./deploy.sh -p gpu             # GPU-accelerated deployment
    ./deploy.sh -d                 # Preview without deploying

NOTES:
    - Auto-detection considers: GPU, RAM, LLM availability
    - First run creates .env from .env.example
    - For GPU profile, install NVIDIA Container Toolkit first
    - Set LLM_API_KEY in .env for OpenAI/Together (use 'not-needed' for LM Studio)

MORE INFO:
    See DEPLOYMENT.md for detailed recipes
EOF
}

# Show profile selection menu
show_profile_selection() {
    header "DEPLOYMENT PROFILE SELECTION"
    
    echo ""
    echo -e "${WHITE}Available profiles:${NC}"
    echo ""
    
    local i=1
    for key in minimal standard cpu gpu; do
        echo -e "  $i. ${PROFILE_COLORS[$key]}${PROFILE_NAMES[$key]}${NC} ($key)"
        echo -e "     ${GRAY}${PROFILE_DESCRIPTIONS[$key]}${NC}"
        echo -e "     ${GRAY}Requires: ~${PROFILE_RAM[$key]} RAM${NC}"
        echo ""
        ((i++))
    done
    
    echo -e "  0. ${RED}Cancel deployment${NC}"
    echo ""
    
    local choice
    while true; do
        read -rp "Select profile (0-4): " choice
        case $choice in
            1) echo "minimal"; return ;;
            2) echo "standard"; return ;;
            3) echo "cpu"; return ;;
            4) echo "gpu"; return ;;
            0) exit 0 ;;
            *) echo "Invalid choice" ;;
        esac
    done
}

# Check if LLM is available
llm_available() {
    local url=$1
    if [[ -z "$url" ]]; then
        return 1
    fi
    curl -s "$url/models" > /dev/null 2>&1
}

# Auto-detect best profile
auto_detect_profile() {
    step "Auto" "Detecting best deployment profile..."
    
    local has_gpu=false
    local has_nvidia_toolkit=false
    local has_llm=false
    local ram_gb
    
    ram_gb=$(get_ram_gb)
    
    # Load LMSTUDIO_URL from .env if present
    if [[ -f .env ]]; then
        source .env 2>/dev/null || true
    fi
    
    if gpu_available; then
        has_gpu=true
        info "GPU: Detected"
    else
        info "GPU: Not detected"
    fi
    
    if nvidia_toolkit_installed; then
        has_nvidia_toolkit=true
        info "NVIDIA Container Toolkit: Installed"
    else
        info "NVIDIA Container Toolkit: Not installed"
    fi
    
    if llm_available "${LMSTUDIO_URL:-}"; then
        has_llm=true
        info "LLM endpoint: Available"
    else
        info "LLM endpoint: Not detected"
    fi
    
    info "System RAM: ~${ram_gb}GB"
    
    # Decision logic
    echo ""
    if [[ "$has_gpu" == true && "$has_nvidia_toolkit" == true && $ram_gb -ge 8 ]]; then
        echo -e "${GREEN}  => Recommended: GPU profile (best performance)${NC}"
        echo "gpu"
    elif [[ $ram_gb -ge 16 && "$has_llm" == true ]]; then
        echo -e "${GREEN}  => Recommended: CPU profile (full features, no GPU needed)${NC}"
        echo "cpu"
    elif [[ $ram_gb -ge 8 ]]; then
        echo -e "${GREEN}  => Recommended: Standard profile (knowledge base support)${NC}"
        echo "standard"
    else
        if [[ "$has_llm" == false ]]; then
            echo -e "${GREEN}  => Recommended: Minimal profile (NO LLM needed!)${NC}"
            echo -e "${GRAY}     Text extraction from web pages and documents works without any LLM.${NC}"
        else
            echo -e "${GREEN}  => Recommended: Minimal profile (lightest setup)${NC}"
        fi
        echo "minimal"
    fi
}

# Check prerequisites
check_prerequisites() {
    local profile=$1
    local all_passed=true
    
    step "Check" "Checking prerequisites..."
    
    # Check Docker
    if command_exists docker; then
        success "Docker installed"
        
        if docker_running; then
            success "Docker daemon running"
            
            if docker compose version >/dev/null 2>&1 || docker-compose version >/dev/null 2>&1; then
                success "Docker Compose available"
            else
                error "Docker Compose not found"
                all_passed=false
            fi
        else
            error "Docker daemon not running"
            all_passed=false
        fi
    else
        error "Docker not installed"
        all_passed=false
    fi
    
    # Profile-specific checks
    if [[ "$profile" == "gpu" ]]; then
        if ! gpu_available; then
            error "NVIDIA GPU not detected"
            all_passed=false
        fi
        
        if ! nvidia_toolkit_installed; then
            error "NVIDIA Container Toolkit not installed"
            warning "Install from: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html"
            all_passed=false
        fi
    fi
    
    $all_passed
}

# Check and create .env file
check_env_file() {
    if [[ ! -f ".env" ]]; then
        step "Config" "Creating .env file..."
        
        if [[ -f ".env.example" ]]; then
            cp ".env.example" ".env"
            success "Created .env from .env.example"
            warning "Please review and customize it before starting services"
            return 1
        else
            error ".env.example not found!"
            return 1
        fi
    fi
    return 0
}

# Deploy services
deploy() {
    local profile=$1
    
    header "DEPLOYING MCP GATEWAY"
    
    echo -e "Profile: ${PROFILE_COLORS[$profile]}${PROFILE_NAMES[$profile]}${NC} ($profile)"
    
    if [[ "$DRY_RUN" == true ]]; then
        echo ""
        echo -e "${MAGENTA}[DRY RUN] Would execute:${NC}"
        echo -e "${GRAY}  docker compose --profile $profile up -d${NC}"
        return
    fi
    
    # Check port availability
    echo ""
    info "Checking port availability..."
    
    declare -A ports=(
        [8000]="Gateway"
        [8080]="SearXNG"
    )
    
    if [[ "$profile" != "minimal" ]]; then
        ports[9200]="OpenSearch"
        ports[5601]="Dashboards"
    fi
    
    if [[ "$profile" == "cpu" ]]; then
        ports[5001]="Docling CPU"
    fi

    if [[ "$profile" == "gpu" ]]; then
        ports[5001]="Docling GPU"
    fi
    
    for port in "${!ports[@]}"; do
        if port_available "$port"; then
            info "Port $port available (${ports[$port]})"
        else
            warning "Port $port in use (${ports[$port]})"
        fi
    done
    
    # Pull images
    echo ""
    step "Pull" "Pulling Docker images..."
    docker compose --profile "$profile" pull
    
    # Start services
    echo ""
    step "Start" "Starting services..."
    docker compose --profile "$profile" up -d
    
    # Wait for health checks
    echo ""
    info "Waiting for services to be healthy..."
    sleep 5
    
    # Show status
    echo ""
    info "Service status:"
    docker compose --profile "$profile" ps
    
    # Show access URLs
    header "DEPLOYMENT COMPLETE"
    echo -e "${WHITE}Services are starting up. Access URLs:${NC}"
    echo -e "  ${CYAN}Gateway:    http://localhost:8000${NC}"
    echo -e "  ${CYAN}SearXNG:    http://localhost:8080${NC}"
    
    if [[ "$profile" != "minimal" ]]; then
        echo -e "  ${CYAN}OpenSearch: http://localhost:9200${NC}"
        echo -e "  ${CYAN}Dashboards: http://localhost:5601${NC}"
    fi
    
    echo ""
    info "Useful commands:"
    echo -e "${GRAY}  View logs:    docker compose --profile $profile logs -f${NC}"
    echo -e "${GRAY}  Stop:         docker compose --profile $profile down${NC}"
    echo -e "${GRAY}  Restart:      docker compose --profile $profile restart${NC}"
}

# ============================================================================
# PARSE ARGUMENTS
# ============================================================================

while [[ $# -gt 0 ]]; do
    case $1 in
        -p|--profile)
            PROFILE="$2"
            shift 2
            ;;
        -d|--dry-run)
            DRY_RUN=true
            shift
            ;;
        -s|--skip-env-check)
            SKIP_ENV_CHECK=true
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

# ============================================================================
# MAIN EXECUTION
# ============================================================================

header "MCP Gateway Deployment v$VERSION"

# Validate profile
if [[ "$PROFILE" != "auto" && "$PROFILE" != "minimal" && "$PROFILE" != "standard" && "$PROFILE" != "cpu" && "$PROFILE" != "gpu" ]]; then
    error "Unknown profile: $PROFILE"
    info "Valid profiles: auto, minimal, standard, cpu, gpu"
    exit 1
fi

# Auto-detect if needed
if [[ "$PROFILE" == "auto" ]]; then
    detected=$(auto_detect_profile)
    PROFILE=$(echo "$detected" | tail -1)
    
    echo ""
    read -rp "Proceed with '$PROFILE' profile? (Y/n/change): " proceed
    if [[ "$proceed" == "change" || "$proceed" == "c" ]]; then
        PROFILE=$(show_profile_selection)
    elif [[ "$proceed" == "n" ]]; then
        exit 0
    fi
fi

# Check prerequisites
if [[ "$SKIP_ENV_CHECK" == false ]]; then
    if ! check_prerequisites "$PROFILE"; then
        echo ""
        read -rp "Prerequisites not met. Continue anyway? (y/N): " continue_anyway
        if [[ "$continue_anyway" != "y" ]]; then
            exit 1
        fi
    fi
fi

# Check .env file
if ! check_env_file; then
    echo ""
    read -rp "Edit .env file now? (Y/n): " edit_now
    if [[ "$edit_now" != "n" ]]; then
        if command_exists nano; then
            nano .env
        elif command_exists vim; then
            vim .env
        else
            echo "Please edit .env file manually and run again."
            exit 0
        fi
    fi
fi

# Deploy
deploy "$PROFILE"
