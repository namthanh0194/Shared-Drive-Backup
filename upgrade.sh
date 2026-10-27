#!/bin/bash
set -euo pipefail
source_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
exec /bin/bash "$source_dir/install.sh" install
