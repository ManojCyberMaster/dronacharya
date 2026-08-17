# DronaCharya server image (home-server role).
# The app itself runs CPU-only (embeddings are small); GPU inference lives in
# the ollama / vllm containers next to it — see docker-compose.yml.
FROM python:3.12-slim

# real curl is the last-resort fetch identity (Anubis-gated doc sites
# fingerprint the TLS client and only genuine curl passes)
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY dronacharya ./dronacharya

RUN pip install --no-cache-dir \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    ".[server]"

ENV DRONACHARYA_HOME=/data
VOLUME /data
EXPOSE 8317

CMD ["dc", "serve", "--host", "0.0.0.0"]
