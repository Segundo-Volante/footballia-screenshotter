FROM python:3.11-slim

# System dependencies for Playwright and OpenCV
RUN apt-get update && apt-get install -y \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libdbus-1-3 libxkbcommon0 libatspi2.0-0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    libgl1-mesa-glx libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers
RUN python -m playwright install chromium

# Copy application
COPY . .

# Create data directories
RUN mkdir -p data recordings logs exports

EXPOSE 8000

# NOTE: For Footballia, use --headless=false which requires a display server.
# For Local File mode, headless works fine.
# Use docker-compose or pass display env for Footballia mode.
CMD ["python", "main.py"]
