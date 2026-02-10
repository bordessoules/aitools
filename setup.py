#!/usr/bin/env python3
"""Setup script for MCP Gateway using uv."""

import subprocess
import sys
from pathlib import Path


def run(cmd, description, shell=True):
    """Run a command and handle errors."""
    print(f"\n{'='*60}")
    print(f"Installing: {description}")
    print(f"Command: {cmd}")
    print('='*60)
    
    result = subprocess.run(cmd, shell=shell, capture_output=False)
    if result.returncode != 0:
        print(f"\n[ERROR] Failed: {description}")
        return False
    print(f"\n[SUCCESS] {description}")
    return True


def check_uv():
    """Check if uv is installed, install if not."""
    result = subprocess.run("uv --version", shell=True, capture_output=True)
    if result.returncode == 0:
        version = result.stdout.decode().strip()
        print(f"[OK] uv installed: {version}")
        return True
    
    print("[INFO] uv not found, installing...")
    return run("pip install uv", "uv package manager")


def main():
    print("="*60)
    print("MCP Gateway Setup")
    print("="*60)
    
    if not Path("src/gateway.py").exists():
        print("[ERROR] Run from project root directory")
        sys.exit(1)
    
    # Check/install uv
    if not check_uv():
        print("[ERROR] Failed to install uv")
        sys.exit(1)
    
    success = True
    
    # Create virtual environment
    if not Path(".venv").exists():
        if not run("uv venv .venv --python=3.11", "Virtual environment"):
            success = False
    else:
        print("[SKIP] Virtual environment already exists")
    
    # Activate venv for subsequent commands (Windows)
    venv_python = ".venv\\Scripts\\python.exe" if sys.platform == "win32" else ".venv/bin/python"
    venv_pip = ".venv\\Scripts\\pip.exe" if sys.platform == "win32" else ".venv/bin/pip"
    
    # Install Python dependencies with uv
    if Path("requirements.txt").exists():
        if not run(f"uv pip install -r requirements.txt", "Python dependencies"):
            success = False
    
    # Install MarkItDown with all extras
    if not run('uv pip install "markitdown[all]" openai', "MarkItDown"):
        success = False
    
    # Install mcp package if not in requirements
    if not run("uv pip install mcp httpx", "MCP SDK"):
        success = False
    
    # Install Node.js deps (optional - for vision)
    if Path("package.json").exists():
        if not run("npm install", "Node.js dependencies (Playwright MCP)"):
            print("[WARN] Node.js install failed - vision extraction will be disabled")
            print("       To enable vision, run: npm install")
    else:
        print("[SKIP] No package.json found - skipping Node.js install")
    
    # Summary
    print("\n" + "="*60)
    if success:
        print("Setup complete!")
        print("\nNext steps:")
        print("1. Copy config: cp .env.example .env")
        print("2. Edit .env with your settings")
        print("3. Start services:")
        print("   docker compose -f docker-compose.opensearch.yml up -d")
        print("4. Start gateway: .\\start.ps1")
        print("")
        print("Deployment tiers:")
        print("  Tier 1 (Full):   Chrome + LM Studio + Docling GPU")
        print("  Tier 2 (Docker): External LM Studio API")
        print("  Tier 3 (Minimal): MarkItDown only (no vision)")
    else:
        print("Setup completed with errors.")
        sys.exit(1)


if __name__ == "__main__":
    main()
