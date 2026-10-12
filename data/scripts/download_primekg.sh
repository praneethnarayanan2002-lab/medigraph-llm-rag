#!/usr/bin/env bash
# Download the PrimeKG files we need from Harvard Dataverse (doi:10.7910/DVN/IXA7BM).
# Chandak, Huang & Zitnik, "Building a knowledge graph to enable precision medicine", Sci Data 2023.
set -euo pipefail

RAW_DIR="$(cd "$(dirname "$0")/.." && pwd)/raw"
mkdir -p "$RAW_DIR"
BASE="https://dataverse.harvard.edu/api/access/datafile"

fetch() {
  local id="$1" name="$2"
  if [[ -s "$RAW_DIR/$name" ]]; then
    echo "skip $name (exists)"
    return
  fi
  echo "downloading $name"
  curl -sSL --fail --retry 3 -o "$RAW_DIR/$name.part" "$BASE/$id"
  mv "$RAW_DIR/$name.part" "$RAW_DIR/$name"
}

fetch 6191270 README.txt
fetch 6180617 nodes.tab
fetch 6180619 drug_features.tab
fetch 6180618 disease_features.tab
fetch 6180616 edges.csv

ls -lh "$RAW_DIR"
