# Touchline: one container serving the web app, WebSocket playback, MCP endpoint and the AI crew.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# The app and its dependencies (editable install, so server/static is served from the source tree).
COPY pyproject.toml README.md ./
COPY simulator simulator
COPY analytics analytics
COPY broadcast broadcast
COPY ingest ingest
COPY touchline touchline
COPY server server
RUN pip install -e . && useradd --create-home touchline && mkdir -p data && chown touchline data
USER touchline

EXPOSE 8000
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
