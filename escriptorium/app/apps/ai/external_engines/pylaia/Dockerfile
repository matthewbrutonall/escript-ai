# Prototype image for a separate PyLaia engine.
# Compose does not reference this file. Nothing in this repository builds it.
# It is not a recognizer. The skeleton server still returns unavailable.
#
# Intended later build, from escriptorium/app/apps/ai:
#   docker build -f external_engines/pylaia/Dockerfile -t escript-ai-pylaia-skeleton .
#
# The server refuses any host outside loopback. This image does not add a
# container bind mode, so the process listens on 127.0.0.1 inside the
# container. Publishing the container port does not reach it.
# The image is not reachable as a service yet.
#
# Model files are not copied. A read-only model mount is future work.
# This image does not read one. No API keys or secrets are copied in.

FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/opt/engine

WORKDIR /opt/engine

RUN pip install --no-cache-dir "pylaia==1.1.2"

COPY __init__.py /opt/engine/ai/__init__.py
COPY htr_engine_contract.py /opt/engine/ai/htr_engine_contract.py
COPY external_engines/__init__.py /opt/engine/ai/external_engines/__init__.py
COPY external_engines/pylaia/__init__.py /opt/engine/ai/external_engines/pylaia/__init__.py
COPY external_engines/pylaia/backend.py /opt/engine/ai/external_engines/pylaia/backend.py
COPY external_engines/pylaia/engine.py /opt/engine/ai/external_engines/pylaia/engine.py
COPY external_engines/pylaia/server.py /opt/engine/ai/external_engines/pylaia/server.py

CMD ["python", "-m", "ai.external_engines.pylaia.server", "--host", "127.0.0.1", "--port", "8766"]
