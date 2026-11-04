# Optional Loghi-HTR line image.
# The Escript AI app image does not build this file and does not install
# Loghi or TensorFlow. From escriptorium/, after LOGHI_MODEL_DIR is set:
#   docker compose -f docker-compose.loghi.yml build
#
# The default command is not a recognizer. The optional Compose file starts
# the entrypoint, which loads one model and keeps the child on CPU.

FROM python:3.11-slim-bookworm@sha256:94a8fee35252d06ab75856f15f12c7bae67b10e8a6da93c7fded82b2610926cd

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/opt/engine

WORKDIR /opt/engine

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgomp1=12.2.0-14+deb12u1 \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY external_engines/loghi/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --require-hashes --root-user-action ignore -r /tmp/requirements.lock \
    && rm /tmp/requirements.lock

RUN curl -fsSL -o /tmp/loghi.tar.gz \
        https://github.com/knaw-huc/loghi-htr/archive/c24c745e9aeb287bedeb6b4edee670ae5c81a3cd.tar.gz \
    && echo "d625eb819c50022cba9ccba647c7ef5b4c681dca59eebc3f17ea091431931b15  /tmp/loghi.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/loghi.tar.gz -C /opt \
    && mv /opt/loghi-htr-c24c745e9aeb287bedeb6b4edee670ae5c81a3cd /opt/loghi \
    && rm /tmp/loghi.tar.gz \
    && test -f /opt/loghi/src/api/app.py \
    && test -f /opt/loghi/src/utils/text.py

COPY __init__.py /opt/engine/ai/__init__.py
COPY htr_engine_contract.py /opt/engine/ai/htr_engine_contract.py
COPY external_engines/__init__.py /opt/engine/ai/external_engines/__init__.py
COPY external_engines/loghi/__init__.py /opt/engine/ai/external_engines/loghi/__init__.py
COPY external_engines/loghi/backend.py /opt/engine/ai/external_engines/loghi/backend.py
COPY external_engines/loghi/check_model.py /opt/engine/ai/external_engines/loghi/check_model.py
COPY external_engines/loghi/client.py /opt/engine/ai/external_engines/loghi/client.py
COPY external_engines/loghi/engine.py /opt/engine/ai/external_engines/loghi/engine.py
COPY external_engines/loghi/layout.py /opt/engine/ai/external_engines/loghi/layout.py
COPY external_engines/loghi/server.py /opt/engine/ai/external_engines/loghi/server.py
COPY external_engines/loghi/entrypoint.sh /opt/engine/ai/external_engines/loghi/entrypoint.sh

RUN chmod 755 /opt/engine/ai/external_engines/loghi/entrypoint.sh

CMD ["python", "-m", "ai.external_engines.loghi.server", "--host", "0.0.0.0", "--port", "8769", "--allow-container-bind"]
