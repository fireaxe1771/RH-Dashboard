# Python 3.11 base image pinned to Debian bookworm for Microsoft ODBC package compatibility
FROM python:3.11-slim-bookworm

# Install system dependencies required for SQL Server connectivity
# Using FreeTDS for pymssql/basic auth and ODBC for Azure AD auth
ENV ACCEPT_EULA=Y
RUN apt-get update && apt-get install -y \
    curl \
    gnupg \
    ca-certificates \
    libsybdb5 \
    freetds-dev \
    unixodbc \
    unixodbc-dev \
    gcc \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
      | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list \
      | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://#' \
      > /etc/apt/sources.list.d/microsoft-prod.list \
    && apt-get update \
    && apt-get install -y msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install pip requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend files
COPY . .

EXPOSE 8001

# Run FastAPI app via Uvicorn server with multiple workers so concurrent
# widget queries are dispatched to separate OS-level processes instead of
# all sharing one thread-pool.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "4"]
