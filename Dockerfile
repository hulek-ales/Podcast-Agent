FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=Europe/Prague

# git kvůli nepovinnému self-update z repa při startu (viz entrypoint.sh)
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

RUN sha256sum /app/requirements.txt | cut -c1-16 > /app/.req-stamp

# Commit a čas buildu — administrace je ukazuje v patičce, takže po restartu
# appky je na první pohled vidět, jestli se natáhl nový image, nebo běží starý.
ARG BUILD_REV=dev
ARG BUILD_TIME=""
ENV PODCAST_BUILD_REV=$BUILD_REV PODCAST_BUILD_TIME=$BUILD_TIME

COPY podcast /app/podcast
COPY config.example.yaml entrypoint.sh /app/
RUN chmod +x /app/entrypoint.sh

# /data = konfigurace, hotové díly, mezivýsledky (volume)
ENV PODCAST_CONFIG=/data/config.yaml
VOLUME ["/data"]
EXPOSE 8089

ENTRYPOINT ["/app/entrypoint.sh"]
