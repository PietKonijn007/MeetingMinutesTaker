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
echo -e "${BOLD}[1/10] Checking Python...${NC}"
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
echo -e "${BOLD}[2/10] Checking Node.js...${NC}"
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

# ─── Install ffmpeg (required by pyannote.audio for diarization) ─────
echo -e "${BOLD}[2.5/10] Checking ffmpeg (required for speaker diarization)...${NC}"
if command -v ffmpeg &>/dev/null; then
    echo -e "  ${GREEN}✓${NC} ffmpeg is installed"
else
    echo -e "  ${YELLOW}! ffmpeg not found. Installing via Homebrew...${NC}"
    if command -v brew &>/dev/null; then
        brew install ffmpeg && echo -e "  ${GREEN}✓${NC} ffmpeg installed" || \
            echo -e "  ${YELLOW}! ffmpeg install failed (speaker diarization will not work)${NC}"
    else
        echo -e "  ${YELLOW}! Homebrew not found. Install ffmpeg manually for diarization to work.${NC}"
    fi
fi

# ─── Install WeasyPrint native deps (required for PDF export) ────────
echo -e "${BOLD}[2.7/10] Checking WeasyPrint native libs (required for PDF export)...${NC}"
if [ "$(uname -s)" = "Darwin" ]; then
    if command -v brew &>/dev/null; then
        WEASY_DEPS=(pango cairo gdk-pixbuf libffi)
        MISSING_DEPS=()
        for dep in "${WEASY_DEPS[@]}"; do
            if ! brew list --formula "$dep" &>/dev/null; then
                MISSING_DEPS+=("$dep")
            fi
        done
        if [ ${#MISSING_DEPS[@]} -eq 0 ]; then
            echo -e "  ${GREEN}✓${NC} pango, cairo, gdk-pixbuf, libffi already installed"
        else
            echo -e "  ${YELLOW}! Installing: ${MISSING_DEPS[*]}${NC}"
            brew install "${MISSING_DEPS[@]}" && \
                echo -e "  ${GREEN}✓${NC} WeasyPrint native libs installed" || \
                echo -e "  ${YELLOW}! WeasyPrint native libs install failed (PDF export will not work)${NC}"
        fi
    else
        echo -e "  ${YELLOW}! Homebrew not found. Install pango/cairo/gdk-pixbuf/libffi manually for PDF export.${NC}"
    fi
else
    echo -e "  ${YELLOW}! Non-macOS: PDF export needs native libs (pango/cairo/gdk-pixbuf/libffi). See WeasyPrint docs for your OS.${NC}"
fi

# ─── Install BlackHole (audio loopback) ──────────────────────────────
echo -e "${BOLD}[3/10] Checking BlackHole 2ch...${NC}"
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
echo -e "${BOLD}[4/10] Setting up Python environment...${NC}"
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

# ─── Install Whisper.cpp with hardware-specific acceleration ────────
echo -e "${BOLD}[5/10] Installing Whisper.cpp transcription engine...${NC}"

OS_NAME="$(uname -s)"
ARCH_NAME="$(uname -m)"
WHISPER_PLATFORM="generic CPU"
WHISPER_ENV_FLAGS=()

if [ "$OS_NAME" = "Darwin" ] && [ "$ARCH_NAME" = "arm64" ]; then
    WHISPER_PLATFORM="Apple Silicon (Metal + Accelerate)"
    # Metal and Accelerate are auto-detected by cmake on Apple Silicon
    WHISPER_ENV_FLAGS+=("WHISPER_METAL=1" "WHISPER_COREML=0")
elif [ "$OS_NAME" = "Darwin" ] && [ "$ARCH_NAME" = "x86_64" ]; then
    WHISPER_PLATFORM="Intel Mac (Accelerate)"
    # Accelerate framework auto-detected
elif [ "$OS_NAME" = "Linux" ]; then
    if command -v nvidia-smi &>/dev/null && nvidia-smi -L &>/dev/null; then
        WHISPER_PLATFORM="Linux + NVIDIA CUDA"
        WHISPER_ENV_FLAGS+=("WHISPER_CUDA=1")
    elif [ -d "/opt/rocm" ]; then
        WHISPER_PLATFORM="Linux + AMD ROCm"
        WHISPER_ENV_FLAGS+=("WHISPER_HIPBLAS=1")
    else
        WHISPER_PLATFORM="Linux CPU (OpenBLAS if available)"
        if command -v pkg-config &>/dev/null && pkg-config --exists openblas 2>/dev/null; then
            WHISPER_ENV_FLAGS+=("WHISPER_OPENBLAS=1")
        fi
    fi
fi

echo "  Hardware: $WHISPER_PLATFORM"

# Check cmake (required to build whisper.cpp)
if ! command -v cmake &>/dev/null; then
    echo -e "  ${YELLOW}! cmake not found.${NC}"
    if [ "$OS_NAME" = "Darwin" ] && command -v brew &>/dev/null; then
        echo "  Installing cmake via Homebrew..."
        brew install cmake &>/dev/null && echo -e "  ${GREEN}✓${NC} cmake installed"
    else
        echo -e "  ${YELLOW}! Skipping whisper.cpp build (install cmake and re-run to enable).${NC}"
        echo -e "  ${YELLOW}  Faster Whisper still works as the default engine.${NC}"
    fi
fi

if command -v cmake &>/dev/null; then
    echo "  Building pywhispercpp from source (2-5 minutes)..."
    # Force source build to pick up hardware accelerators (wheel may not have them)
    BUILD_CMD="pip install --quiet --no-binary=pywhispercpp pywhispercpp psutil"
    if [ ${#WHISPER_ENV_FLAGS[@]} -gt 0 ]; then
        BUILD_CMD="${WHISPER_ENV_FLAGS[*]} $BUILD_CMD"
    fi
    if eval "$BUILD_CMD" 2>/dev/null; then
        echo -e "  ${GREEN}✓${NC} Whisper.cpp installed ($WHISPER_PLATFORM)"
    else
        echo -e "  ${YELLOW}! Whisper.cpp build failed (Faster Whisper still works as default)${NC}"
        # Still try to install psutil for hardware detection
        pip install --quiet psutil 2>/dev/null || true
    fi
fi

# ─── Build frontend ─────────────────────────────────────────────────
echo -e "${BOLD}[6/10] Building web frontend...${NC}"
cd web
if [ ! -d "node_modules" ]; then
    npm install --silent 2>/dev/null
fi
npm run build --silent 2>/dev/null
cd ..
echo -e "  ${GREEN}✓${NC} Frontend built"

# ─── Initialize database ────────────────────────────────────────────
echo -e "${BOLD}[7/10] Initializing database...${NC}"
.venv/bin/mm init 2>/dev/null
echo -e "  ${GREEN}✓${NC} Database initialized"

# ─── Configure API keys ─────────────────────────────────────────────
echo -e "${BOLD}[8/10] Configuring API keys...${NC}"
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
echo -e "${BOLD}[9/10] Setting up auto-start service...${NC}"
.venv/bin/mm service install 2>/dev/null && \
    echo -e "  ${GREEN}✓${NC} Service installed (auto-starts on login)" || \
    echo -e "  ${YELLOW}! Service install skipped (run 'mm service install' later)${NC}"

# ─── Symlink mm command ─────────────────────────────────────────────
echo -e "${BOLD}[10/10] Making 'mm' command available...${NC}"
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
