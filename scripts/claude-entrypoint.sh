#!/bin/sh
set -eu
if [ "${CODEX_LB_DATABASE_MIGRATE_ON_STARTUP:-true}" = "true" ]; then
    python -m app.db.migrate upgrade
fi
export CODEX_LB_DATABASE_MIGRATE_ON_STARTUP=false
exec python -m app.claude_entrypoint --host 0.0.0.0 --port 2455
