FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=Europe/Prague

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY podcast /app/podcast
COPY config.example.yaml entrypoint.sh /app/
RUN chmod +x /app/entrypoint.sh

# /data = konfigurace, hotové díly, mezivýsledky (volume)
ENV PODCAST_CONFIG=/data/config.yaml
VOLUME ["/data"]

ENTRYPOINT ["/app/entrypoint.sh"]
