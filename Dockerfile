# syntax=docker/dockerfile:1
#
# cloud-control-bot container image.
#
# ICMP ping (ping3) needs a raw socket. Instead of running as root, the python
# interpreter is granted CAP_NET_RAW via file capabilities, so an unprivileged
# user can open raw ICMP sockets. The container must still be started with
# NET_RAW in its capability set (see docker-compose.yml: cap_add: [NET_RAW]).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install runtime dependencies first (cached unless requirements.txt changes).
COPY requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Grant CAP_NET_RAW to the interpreter so a non-root user can send ICMP pings.
# libcap2-bin (provides setcap) is removed afterwards — the capability persists
# on the binary itself.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends libcap2-bin; \
    setcap cap_net_raw+ep "$(readlink -f "$(command -v python3)")"; \
    apt-get purge -y libcap2-bin; \
    apt-get autoremove -y; \
    rm -rf /var/lib/apt/lists/*

# Copy the application source.
COPY . .

# Run as an unprivileged user; it owns the runtime data/log directories.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/data /app/logs \
    && chown -R appuser:appuser /app
USER appuser

CMD ["python", "main.py"]
