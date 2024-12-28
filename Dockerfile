# Dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY notion_d_day_label.py .
COPY entrypoint.sh .

RUN chmod +x entrypoint.sh notion_d_day_label.py

ENTRYPOINT ["/app/entrypoint.sh"]
