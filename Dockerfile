# Use official Python slim image for a smaller, secure footprint
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Set the working directory
WORKDIR /app

# Install system dependencies (if any are needed for Python packages like mysqlclient or cryptography)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install them
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Install the DaiBai package with API dependencies (fastapi, uvicorn) and default LLM
RUN pip install --no-cache-dir -e ".[gui,gemini]"

# Expose the port the API runs on (assuming 8000 for standard FastAPI)
EXPOSE 8000

# Run the DaiBai API server via uvicorn
CMD ["uvicorn", "daibai.api.server:app", "--host", "0.0.0.0", "--port", "8000"]
