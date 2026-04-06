#!/usr/bin/env bash
set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color
BOLD='\033[1m'

echo ""
echo -e "${BLUE}${BOLD}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}${BOLD}║   Meeting Minutes Taker — Installation       ║${NC}"
echo -e "${BLUE}${BOLD}╚══════════════════════════════════════════════╝${NC}"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ─── Check Python ────────────────────────────────────────────────────
echo -e "${BOLD}[1/9] Checking Python...${NC}"
PYTHON=""
for p in python3.12 python3.11 python3; do
    if command -v "$p" &>/dev/null; then
        version=$("$p" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
            PYTHON="$p"
            echo -e "  ${GREEN}✓${NC} Found $p ($version)"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo -e "  ${RED}✗ Python 3.11+ not found.${NC}"
    echo "  Install with: brew install python@3.12"
    exit 1
fi

# ─── Check Node.js ───────────────────────────────────────────────────
echo -e "${BOLD}[2/9] Checking Node.js...${NC}"
if command -v node &>/dev/null; then
    NODE_VERSION=$(node --version)
    echo -e "  ${GREEN}✓${NC} Found Node.js $NODE_VERSION"
else
    echo -e "  ${YELLOW}! Node.js not found. Installing via Homebrew...${NC}"
    if command -v brew &>/dev/null; then
        brew install node
        echo -e "  ${GREEN}✓${NC} Node.js installed"
    else
        echo -e "  ${RED}✗ Homebrew not found. Install Node.js manually: https://nodejs.org${NC}"
        exit 1
    fi
fi

# ─── Install BlackHole (audio loopback) ──────────────────────────────
echo -e "${BOLD}[3/9] Checking BlackHole 2ch...${NC}"
if system_profiler SPAudioDataType 2>/dev/null | grep -q "BlackHole" || \
   ls /Library/Audio/Plug-Ins/HAL/ 2>/dev/null | grep -qi "blackhole"; then
    echo -e "  ${GREEN}✓${NC} BlackHole 2ch is installed"
else
    echo -e "  ${YELLOW}! BlackHole 2ch not found. Installing...${NC}"
    if command -v brew &>/dev/null; then
        brew install --cask blackhole-2ch || true
        echo -e "  ${GREEN}✓${NC} BlackHole 2ch installed"
    else
        echo -e "  ${YELLOW}! Install BlackHole manually: https://existentialaudio.com/blackhole/${NC}"
    fi
fi

# ─── Create Python virtual environment ───────────────────────────────
echo -e "${BOLD}[4/9] Setting up Python environment...${NC}"
if [ ! -d ".venv" ]; then
    "$PYTHON" -m venv .venv
    echo -e "  ${GREEN}✓${NC} Virtual environment created"
else
    echo -e "  ${GREEN}✓${NC} Virtual environment exists"
fi

# Activate venv
source .venv/bin/activate

# Install/upgrade pip
pip install --quiet --upgrade pip

# Install the package
echo "  Installing dependencies (this may take a few minutes)..."
pip install --quiet -e ".[dev]"
echo -e "  ${GREEN}✓${NC} Python dependencies installed"

# ─── Build frontend ─────────────────────────────────────────────────
echo -e "${BOLD}[5/9] Building web frontend...${NC}"
cd web
if [ ! -d "node_modules" ]; then
    npm install --silent 2>/dev/null
fi
npm run build --silent 2>/dev/null
cd ..
echo -e "  ${GREEN}✓${NC} Frontend built"

# ─── Initialize database ────────────────────────────────────────────
echo -e "${BOLD}[6/9] Initializing database...${NC}"
.venv/bin/mm init 2>/dev/null
echo -e "  ${GREEN}✓${NC} Database initialized"

# ─── Configure API keys ─────────────────────────────────────────────
echo -e "${BOLD}[7/9] Configuring API keys...${NC}"
if [ -f ".env" ]; then
    echo -e "  ${GREEN}✓${NC} .env file exists"
else
    echo ""
    echo -e "  ${BOLD}API keys are required for meeting minutes generation.${NC}"
    echo ""

    # Anthropic key
    read -p "  Anthropic API key (from console.anthropic.com): " ANTHROPIC_KEY

    # HuggingFace token
    read -p "  HuggingFace token (from huggingface.co/settings/tokens, optional): " HF_TOKEN

    # Write .env
    {
        echo "ANTHROPIC_API_KEY=$ANTHROPIC_KEY"
        [ -n "$HF_TOKEN" ] && echo "HF_TOKEN=$HF_TOKEN"
    } > .env

    echo -e "  ${GREEN}✓${NC} .env file created"
fi

# ─── Install macOS service ───────────────────────────────────────────
echo -e "${BOLD}[8/9] Setting up auto-start service...${NC}"
.venv/bin/mm service install 2>/dev/null && \
    echo -e "  ${GREEN}✓${NC} Service installed (auto-starts on login)" || \
    echo -e "  ${YELLOW}! Service install skipped (run 'mm service install' later)${NC}"

# ─── Symlink mm command ─────────────────────────────────────────────
echo -e "${BOLD}[9/9] Making 'mm' command available...${NC}"
LINK_DIR="/usr/local/bin"
if [ -w "$LINK_DIR" ] || [ -w "$(dirname "$LINK_DIR")" ]; then
    ln -sf "$SCRIPT_DIR/.venv/bin/mm" "$LINK_DIR/mm"
    echo -e "  ${GREEN}✓${NC} Linked mm → $LINK_DIR/mm"
else
    sudo ln -sf "$SCRIPT_DIR/.venv/bin/mm" "$LINK_DIR/mm"
    echo -e "  ${GREEN}✓${NC} Linked mm → $LINK_DIR/mm (via sudo)"
fi

# ─── Done ────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}║   Installation Complete!                     ║${NC}"
echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}Quick start:${NC}"
echo -e "    mm serve              # Start the server"
echo -e "    open http://localhost:8080   # Open the web UI"
echo ""
echo -e "  ${BOLD}Or use the auto-start service:${NC}"
echo -e "    mm service start      # Start now"
echo -e "    mm service status     # Check status"
echo -e "    mm service logs       # View logs"
echo ""
echo -e "  ${BOLD}Audio setup:${NC}"
echo -e "    Open Audio MIDI Setup and create:"
echo -e "    1. Multi-Output Device (speakers + BlackHole) → set as system output"
echo -e "    2. Aggregate Device (mic + BlackHole) → select in the app"
echo -e "    See docs/USER_GUIDE.md for detailed instructions."
echo ""
echo -e "  ${BOLD}Record your first meeting:${NC}"
echo -e "    1. Open http://localhost:8080/record"
echo -e "    2. Select your audio device"
echo -e "    3. Click the red Record button"
echo ""
