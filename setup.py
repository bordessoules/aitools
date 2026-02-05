#!/usr/bin/env python3
"""Setup script for MCP Gateway - installs both Python and Node.js dependencies."""

import subprocess
import sys
from pathlib import Path


def run(cmd, description):
    """Run a command and handle errors."""
    print(f"\n{'='*60}")
    print(f"Installing: {description}")
    print(f"Command: {cmd}")
    print('='*60)
    
    result = subprocess.run(cmd, shell=True, capture_output=False)
    if result.returncode != 0:
        print(f"\n[ERROR] Failed to install: {description}")
        return False
    print(f"\n[SUCCESS] {description}")
    return True


def main():
    print("="*60)
    print("MCP Gateway Setup")
    print("="*60)
    
    # Check if we're in the right directory
    if not Path("src/gateway.py").exists():
        print("[ERROR] Please run this script from the project root directory")
        sys.exit(1)
    
    success = True
    
    # 1. Install Python dependencies
    if not run("pip install -r requirements.txt", "Python dependencies"):
        success = False
    
    # 2. Install Node.js dependencies (Playwright MCP)
    if Path("package.json").exists():
        if not run("npm install", "Node.js dependencies (Playwright MCP)"):
            success = False
    else:
        print("\n[SKIP] No package.json found - skipping Node.js install")
    
    # Summary
    print("\n" + "="*60)
    if success:
        print("Setup complete!")
        print("\nNext steps:")
        print("1. cp .env.example .env")
        print("2. Edit .env with your settings")
        print("3. Start services: docker-compose --profile gpu up -d")
        print("4. Start gateway: python -m src.gateway -t sse -p 8000")
    else:
        print("Setup completed with errors. Check output above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
