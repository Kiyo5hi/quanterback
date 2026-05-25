FROM python:3.12-slim

# System deps for pandas/yfinance/SSL
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

# supercronic — container-native cron (multi-arch)
ARG SUPERCRONIC_VERSION=0.2.29
ARG SUPERCRONIC_SHA1_AMD64=cd48d45c4b10f3f0bfdd3a57d054cd05ac96812b
ARG SUPERCRONIC_SHA1_ARM64=512f6736450c56555e01b363144c3c9d23abed4c
ARG TARGETARCH
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) sha1="${SUPERCRONIC_SHA1_AMD64}"; arch="amd64" ;; \
      arm64) sha1="${SUPERCRONIC_SHA1_ARM64}"; arch="arm64" ;; \
      *) echo "Unsupported arch: ${TARGETARCH}"; exit 1 ;; \
    esac; \
    curl -fsSLo /usr/local/bin/supercronic \
      "https://github.com/aptible/supercronic/releases/download/v${SUPERCRONIC_VERSION}/supercronic-linux-${arch}"; \
    echo "${sha1}  /usr/local/bin/supercronic" | sha1sum -c -; \
    chmod +x /usr/local/bin/supercronic

# uv for fast dependency install
RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml ./
RUN uv pip install --system -e ".[dev]"

COPY src/ ./src/
COPY tests/ ./tests/
COPY config/ /config/
COPY docker/crontab /app/docker/crontab
COPY docker/cron/ /app/docker/cron/
COPY docker/entrypoint.sh /app/docker/entrypoint.sh
RUN chmod +x /app/docker/entrypoint.sh

# Re-install to pick up src/
RUN uv pip install --system -e ".[dev]"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=America/New_York

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["quanterback"]
