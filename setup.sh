#!/bin/bash
set -e

echo ""
echo "Footballia Screenshotter — Setup"
echo "===================================="
echo ""

# Check Python
if ! command -v python3 &>/dev/null; then
    echo "Python 3.10+ is required but not found."
    echo "   Install from: https://python.org"
    exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Python ${PY_VERSION} found"

# Create virtual environment
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi
source venv/bin/activate
echo "Virtual environment activated"

# Install dependencies
echo "Installing Python packages..."
pip install -q -r requirements.txt

# Install Playwright browser
echo "Installing Chromium for Playwright..."
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    python -m playwright install --with-deps chromium
else
    python -m playwright install chromium
fi
echo "Chromium installed"

# Check for .env file
if [ ! -f ".env" ]; then
    echo ""
    echo "Creating .env file (edit to add your API keys)..."
    cat > .env << 'EOF'
# OpenAI (required for GPT-4o-mini classification)
OPENAI_API_KEY=

# Google Gemini (optional — free tier available)
# Get a key at: https://aistudio.google.com/apikey
GEMINI_API_KEY=

# Neither key is needed for Manual classification mode
EOF
    echo ".env created — add your API keys there"
fi

echo ""
echo "===================================="
echo "Setup complete!"
echo ""
echo "To start:"
echo "  source venv/bin/activate"
echo "  python main.py"
echo ""
echo "Then open http://localhost:8000 in your browser."
echo ""
