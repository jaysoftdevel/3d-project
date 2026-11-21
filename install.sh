#!/usr/bin/env bash
set -euo pipefail
installer_root=$(cd -- "$(dirname -- "$0")" && pwd -P)
if ! command -v python3 >/dev/null 2>&1; then
    printf 'Python 3 is required to validate this installer. Install Python 3, then run install.sh again.\n' >&2
    exit 2
fi
export PYTHONDONTWRITEBYTECODE=1
exec python3 "$installer_root/installer.py" "$@"
