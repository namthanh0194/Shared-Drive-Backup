#!/bin/bash
set -euo pipefail
source_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
exec /www/server/panel/pyenv/bin/python -B "$source_dir/cli.py" unregister
