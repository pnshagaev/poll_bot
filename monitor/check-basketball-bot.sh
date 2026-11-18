#!/usr/bin/env bash
set -Eeuo pipefail
# Python keeps tokens out of process arguments and safely extracts journal errors.
exec /usr/bin/python3 "$(dirname -- "${BASH_SOURCE[0]}")/notify.py" "$@"
