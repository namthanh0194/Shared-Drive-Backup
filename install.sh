#!/bin/bash
set -euo pipefail
umask 077
source_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
python_bin=/www/server/panel/pyenv/bin/python
test -x "$python_bin" || { echo 'Cần Python environment của aaPanel.' >&2; exit 1; }
if [[ "${1:-install}" == uninstall ]]; then
  exec "$python_bin" -B "$source_dir/cli.py" unregister
fi
exec "$python_bin" -B "$source_dir/tools/install_plugin.py"
