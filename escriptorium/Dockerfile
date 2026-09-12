# Escript AI image, built from this tree (not the official eScriptorium image).
#   docker compose build
#   docker compose up -d
#
# Frontend is webpack production (hashed bundles + webpack-stats.json).
# Python deps come from app/requirements.txt (kraken 7.x, drf-spectacular).

FROM docker.io/library/node:20-bookworm-slim AS frontend
WORKDIR /build
COPY front/package.json front/package-lock.json ./
RUN npm ci
COPY front/ ./
RUN npm run production

FROM docker.io/library/python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    FRONTEND_DIR=/usr/src/app/front

WORKDIR /usr/src/app

RUN apt-get update -q && \
    apt-get install -y --no-install-recommends \
        netcat-traditional jpegoptim pngcrush libvips libffi-dev \
        git build-essential default-jre gettext curl \
        libxml2 libxslt1.1 libpq5 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN addgroup --system uwsgi && \
    adduser --system --no-create-home --ingroup uwsgi uwsgi

COPY app/requirements.txt /usr/src/app/requirements.txt
RUN pip install --root-user-action ignore --upgrade pip && \
    pip install --root-user-action ignore --no-cache-dir -r requirements.txt

COPY app/entrypoint.sh /usr/src/app/entrypoint.sh
COPY app/manage.py /usr/src/app/manage.py
COPY app/uwsgi.ini /usr/src/app/uwsgi.ini
COPY app/apps /usr/src/app/apps
COPY app/escriptorium /usr/src/app/escriptorium
COPY app/locale /usr/src/app/locale
COPY app/homepage /usr/src/app/homepage
COPY app/contributors /usr/src/app/contributors
COPY --from=frontend /build/dist /usr/src/app/front

RUN chmod +x /usr/src/app/entrypoint.sh && \
    python /usr/src/app/locale/compile_homepage_mo.py

ENTRYPOINT ["/usr/src/app/entrypoint.sh"]
