FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=18473

WORKDIR /app

RUN useradd --create-home --shell /usr/sbin/nologin appuser

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
RUN mkdir -p /app/data

RUN chown -R appuser:appuser /app
USER appuser

VOLUME ["/app/data"]

EXPOSE 18473

CMD ["sh", "-c", "uvicorn app.server:app --host 0.0.0.0 --port ${PORT:-18473}"]
