# Python 3.11 base image with minimal OS footprint
FROM python:3.11-slim

# Install system dependencies required for FreeTDS (needed for pymssql Azure SQL driver)
RUN apt-get update && apt-get install -y \
    libsybdb5 \
    freetds-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install pip requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend files
COPY . .

EXPOSE 8000

# Run FastAPI app via Uvicorn server
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
