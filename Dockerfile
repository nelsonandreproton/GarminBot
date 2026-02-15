FROM python:3.13-slim

# System dependencies for pyzbar (barcode decoding) and matplotlib
RUN apt-get update && \
    apt-get install -y --no-install-recommends libzbar0 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# Data and logs are mounted as volumes
VOLUME ["/app/data", "/app/logs"]

CMD ["python", "-m", "src.main"]
