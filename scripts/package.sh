#!/usr/bin/env bash
set -euo pipefail

rm -rf build
mkdir -p build
(cd app && zip -q -r ../build/app.zip .)
echo "Created build/app.zip"
