# Optional Calamari line image.
# The Escript AI app image does not build this file and does not install
# Calamari or TensorFlow. From escriptorium/, after CALAMARI_MODEL_DIR is set:
#   docker compose -f docker-compose.calamari.yml build
#
# The default command is not a recognizer. The optional Compose file starts
# predict mode, requires a licence label, and keeps the child on CPU.

FROM python:3.10-slim-bookworm@sha256:5be5aaecb962a41567bc8041f1e9e3432cb085b037fe057f9a562630b2e4b370

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/opt/engine

WORKDIR /opt/engine

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1=12.2.0-14+deb12u1 \
    && rm -rf /var/lib/apt/lists/*

COPY external_engines/calamari/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir --root-user-action ignore -r /tmp/requirements.lock \
    && rm /tmp/requirements.lock

COPY __init__.py /opt/engine/ai/__init__.py
COPY htr_engine_contract.py /opt/engine/ai/htr_engine_contract.py
COPY external_engines/__init__.py /opt/engine/ai/external_engines/__init__.py
COPY external_engines/calamari/__init__.py /opt/engine/ai/external_engines/calamari/__init__.py
COPY external_engines/calamari/backend.py /opt/engine/ai/external_engines/calamari/backend.py
COPY external_engines/calamari/check_model.py /opt/engine/ai/external_engines/calamari/check_model.py
COPY external_engines/calamari/engine.py /opt/engine/ai/external_engines/calamari/engine.py
COPY external_engines/calamari/predictor.py /opt/engine/ai/external_engines/calamari/predictor.py
COPY external_engines/calamari/server.py /opt/engine/ai/external_engines/calamari/server.py

CMD ["python", "-m", "ai.external_engines.calamari.server", "--host", "0.0.0.0", "--port", "8768", "--allow-container-bind"]
