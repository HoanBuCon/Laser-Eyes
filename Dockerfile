# ==============================================================================
# VIGIL AI Enterprise Proctoring System Dockerfile
# Optimized multi-stage build for High-Performance Computer Vision & FastAPI REST
# ==============================================================================

FROM python:3.11-slim AS runtime

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PORT=8000

WORKDIR /app

# Install essential system dependencies for OpenCV and MediaPipe headless execution
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    ffmpeg \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy project source code
COPY . .

# Ensure data and models directories exist
RUN mkdir -p data/evidence data/sessions data/uploads models reports

# Expose API and Dashboard port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Launch production server runner
CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "8000"]
