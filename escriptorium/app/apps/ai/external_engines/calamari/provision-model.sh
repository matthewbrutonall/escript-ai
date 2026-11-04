#!/bin/sh
# Download the pinned antiqua_historical release and install it outside the
# repository. A checksum mismatch leaves the destination unchanged.
set -eu
if [ "$#" -ne 2 ]; then
  echo "usage: provision-model.sh DESTINATION_PARENT ARCHIVE_DIR" >&2
  exit 2
fi
destination_parent=$1
archive_dir=$2
apps_dir=$(CDPATH= cd -- "$(dirname "$0")/../../.." && pwd)
export PYTHONPATH="$apps_dir${PYTHONPATH:+:$PYTHONPATH}"
archive="$archive_dir/antiqua_historical-2.2.tar.gz"
url="https://github.com/Calamari-OCR/calamari_models/releases/download/2.2/antiqua_historical.tar.gz"
mkdir -p "$archive_dir"
if [ ! -f "$archive" ]; then
  curl -fsSL -o "$archive.partial" "$url" || {
    rm -f "$archive.partial"
    exit 2
  }
  if ! python3 -m ai.external_engines.calamari.provision_model --check "$archive.partial"; then
    rm -f "$archive.partial"
    exit 2
  fi
  mv "$archive.partial" "$archive"
fi
if ! python3 -m ai.external_engines.calamari.provision_model "$archive" "$destination_parent/antiqua_historical"; then
  if ! python3 -m ai.external_engines.calamari.provision_model --check "$archive"; then
    rm -f "$archive"
  fi
  exit 2
fi
