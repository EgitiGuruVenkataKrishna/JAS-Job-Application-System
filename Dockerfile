# ============================================================
# JAS — Cloud-Native AI Job Application System
# Lightweight image: ~400MB (no torch/transformers)
# ============================================================
FROM python:3.11-slim

# System dependencies for Playwright + LaTeX
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Playwright Chromium dependencies
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libatspi2.0-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libwayland-client0 \
    # LaTeX compiler
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Tectonic (lightweight LaTeX compiler)
RUN curl --proto '=https' --tlsv1.2 -fsSL https://drop-sh.fullyjustified.net | sh \
    && mv tectonic /usr/local/bin/

WORKDIR /app

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]"

# Install Playwright Chromium
RUN playwright install chromium

# Copy application code
COPY . .

# Create output directories
RUN mkdir -p output/resumes output/cover_letters output/screenshots

# Expose port for FastAPI (default 8080 for Cloud Run)
ENV PORT=8080
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import os, httpx; port = os.environ.get('PORT', '8080'); httpx.get(f'http://localhost:{port}/health').raise_for_status()" || exit 1

# Run using shell execution to evaluate the dynamic $PORT environment variable at runtime
CMD ["sh", "-c", "uvicorn src.main:app --host 0.0.0.0 --port ${PORT}"]

