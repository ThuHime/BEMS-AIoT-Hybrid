FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install small OS tools used by TensorFlow wheels and health diagnostics.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgomp1 ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip && \
    python -m pip install -r requirements.txt

COPY backend/server_ai_bems.py ./server_ai_bems.py
RUN mkdir -p /app/models

CMD ["python", "-u", "server_ai_bems.py"]
