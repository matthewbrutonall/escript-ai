# Prototype image for a separate PyLaia engine.
# Compose does not reference this file. Nothing in this repository builds it.
# It is not a recognizer. The default command still returns unavailable.
# --backend decode is not part of this command.
#
# Intended later build, from escriptorium/app/apps/ai:
#   docker build -f external_engines/pylaia/Dockerfile -t escript-ai-pylaia-skeleton .
#
# Without --allow-container-bind the server stays on 127.0.0.1.
# The command below opts in. It listens on 0.0.0.0 inside the container,
# so a deliberately started container is reachable on the Docker network.
# The flag allows only 0.0.0.0. Compose does not start this image.
# It is still not a recognizer.
#
# Model files are not copied. The default command does not read a model mount.
# A process reads one only when started with --backend decode, and that
# directory should be mounted read-only. No API keys or secrets are copied in.
#
# laia/__init__.py runs git during import. which("git") is None on this
# base image, and that raises TypeError. git lets the probe fail softly.
# The skeleton server still does not import laia.

FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/opt/engine

WORKDIR /opt/engine

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "pylaia==1.1.2"

COPY __init__.py /opt/engine/ai/__init__.py
COPY htr_engine_contract.py /opt/engine/ai/htr_engine_contract.py
COPY external_engines/__init__.py /opt/engine/ai/external_engines/__init__.py
COPY external_engines/pylaia/__init__.py /opt/engine/ai/external_engines/pylaia/__init__.py
COPY external_engines/pylaia/backend.py /opt/engine/ai/external_engines/pylaia/backend.py
COPY external_engines/pylaia/engine.py /opt/engine/ai/external_engines/pylaia/engine.py
COPY external_engines/pylaia/server.py /opt/engine/ai/external_engines/pylaia/server.py
COPY external_engines/pylaia/decoder.py /opt/engine/ai/external_engines/pylaia/decoder.py

CMD ["python", "-m", "ai.external_engines.pylaia.server", "--host", "0.0.0.0", "--port", "8766", "--allow-container-bind"]
