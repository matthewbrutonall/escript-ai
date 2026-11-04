#!/bin/sh
# Start the Loghi API on loopback, then the line-contract server.
# The API log stays in the work directory. This script does not print it.

set -eu

mkdir -p /work/output
cd /opt/loghi/src/api
python app.py > /work/loghi-api.log 2>&1 &
loghi_pid=$!

cleanup() {
    kill "$loghi_pid" 2>/dev/null || true
    wait "$loghi_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

ready=0
i=0
while [ "$i" -lt 180 ]; do
    if ! kill -0 "$loghi_pid" 2>/dev/null; then
        echo "FAILED: loghi_api" >&2
        exit 2
    fi
    if grep -q "Error loading model" /work/loghi-api.log 2>/dev/null \
        || grep -q "Failed to load model" /work/loghi-api.log 2>/dev/null \
        || grep -q "Error loading tokenizer" /work/loghi-api.log 2>/dev/null; then
        echo "FAILED: model_load" >&2
        exit 2
    fi
    if grep -q "loaded successfully" /work/loghi-api.log 2>/dev/null; then
        ready=1
        break
    fi
    i=$((i + 1))
    sleep 2
done
if [ "$ready" -ne 1 ]; then
    echo "FAILED: loghi_api" >&2
    exit 2
fi

trap - EXIT INT TERM
exec python -m ai.external_engines.loghi.server \
    --host 0.0.0.0 \
    --port 8769 \
    --allow-container-bind \
    --backend predict \
    --require-licence \
    --model-dir /models/current \
    --work-root /work \
    --output-dir /work/output \
    --loghi-url http://127.0.0.1:5000 \
    --timeout "${LOGHI_CONTRACT_TIMEOUT:-1600}"
