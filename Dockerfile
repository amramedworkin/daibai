# =============================================================================
# DaiBai - Multi-Stage, CPU-Optimized Dockerfile
# Phase 4 Step 1: Optimized Containerization
#
# Strategy:
# 1. Force CPU-only PyTorch (~150MB vs ~4GB with CUDA)
# 2. Bake embedding model (all-MiniLM-L6-v2) into image to avoid 2-3 min startup
# 3. Slim runtime stage for production deployment
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: Builder
# -----------------------------------------------------------------------------
FROM python:3.10-slim AS builder
WORKDIR /app

# Install system build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency specs and project
COPY pyproject.toml README.md ./
COPY daibai/ daibai/
COPY scripts/ scripts/

# Install Python dependencies with CPU-only PyTorch (saves ~4GB)
# --extra-index-url makes pip prefer CPU wheels from PyTorch
RUN pip install --user --no-cache-dir \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    -e ".[cache,gui]"

# Pre-download the embedding model (avoids 2-3 min startup at first run)
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# -----------------------------------------------------------------------------
# Stage 2: Runtime
# -----------------------------------------------------------------------------
FROM python:3.10-slim

WORKDIR /app

# Copy installed dependencies and baked model from builder
COPY --from=builder /root/.local /root/.local
COPY --from=builder /root/.cache/huggingface /root/.cache/huggingface
ENV PATH=/root/.local/bin:$PATH

# Copy application code
COPY --from=builder /app/daibai /app/daibai
COPY --from=builder /app/scripts /app/scripts
COPY --from=builder /app/pyproject.toml /app/
COPY --from=builder /app/README.md /app/

# Headless/container operation
ENV PYTHONUNBUFFERED=1
ENV DAIBAI_ENV=production
ENV PYTHONPATH=/app

# Entrypoint: CLI by default (API server via daibai-server when needed)
ENTRYPOINT ["python", "-m", "daibai"]
