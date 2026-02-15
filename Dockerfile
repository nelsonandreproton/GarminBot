FROM python:3.13-slim

LABEL maintainer="Nelson Andre"
LABEL description="GarminBot - Garmin Connect to Telegram health bot"

# System dependencies for pyzbar (barcode decoding)
RUN apt-get update && \
    apt-get install -y --no-install-recommends libzbar0 && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user (uid 1000 matches host garminbot user from server-setup.sh)
RUN groupadd -r -g 1000 garminbot && \
    useradd -r -g garminbot -u 1000 -d /app -s /sbin/nologin garminbot

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# Ensure data/logs dirs exist with correct ownership
RUN mkdir -p /app/data /app/logs && chown -R garminbot:garminbot /app

VOLUME ["/app/data", "/app/logs"]

USER garminbot

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

CMD ["python", "-m", "src.main"]
