FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
RUN pip install --no-cache-dir --no-compile \
    "curl_cffi==0.15.0" "requests>=2.32,<3" "httpx[http2,socks]" "loguru"
COPY integrations/__init__.py /app/integrations/__init__.py
COPY integrations/paypal_protocol.py /app/integrations/paypal_protocol.py
COPY integrations/paypal_protocol_server.py /app/integrations/paypal_protocol_server.py
COPY integrations/card_protocol.py /app/integrations/card_protocol.py
COPY integrations/account_run_guard.py /app/integrations/account_run_guard.py
COPY integrations/paypal_countries.json /app/integrations/paypal_countries.json
COPY internal/paypalprotocol/protocol /app/internal/paypalprotocol/protocol
COPY internal/cardprotocol /app/internal/cardprotocol

CMD ["python3", "-m", "integrations.paypal_protocol_server"]
