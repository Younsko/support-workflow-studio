FROM node:24-bookworm-slim AS frontend-build

WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN npm --prefix frontend ci
COPY frontend ./frontend
RUN mkdir -p backend/app/static
RUN npm --prefix frontend run build

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SUPPORT_WORKFLOW_DB_PATH=/var/lib/support-workflow/state.sqlite3

WORKDIR /app
COPY pyproject.toml ./
COPY backend ./backend
RUN rm -rf backend/app/static
COPY --from=frontend-build /src/backend/app/static ./backend/app/static
RUN python -m pip install --no-cache-dir '.[postgres]' \
    && mkdir -p /var/lib/support-workflow \
    && chown -R 10001:10001 /var/lib/support-workflow

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3)" || exit 1

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
