FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY internal/cardprotocol/portal/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --no-compile -r /app/requirements.txt "curl_cffi==0.15.0"
COPY internal/cardprotocol/portal /app
RUN mkdir -p /app/data \
    && python -m compileall -q /app \
    && python -c "import ph_checkout_protocol, provider_checkout, stripe_checkout"

CMD ["sh", "-c", "exec gunicorn --workers 1 --threads 12 --bind 127.0.0.1:${CARD_PAYMENT_PORT} app:app"]
