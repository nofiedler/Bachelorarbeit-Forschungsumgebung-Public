#!/bin/sh
set -eu
if ! python -m research_env migrate; then
    if [ "${RESEARCH_COMPONENT:-}" != "web" ]; then
        echo "Start gesperrt: Datenbankmigration fehlgeschlagen; Logs und Kontrollvolume prüfen." >&2
        exit 1
    fi
    # Keep the local diagnosis reachable. Health stays failed; Compose cannot
    # start the worker while this web process is in diagnostic mode.
    export RESEARCH_MIGRATION_FAILED=1
    echo "Web startet im Diagnosemodus: Datenbankmigration fehlgeschlagen; Worker bleibt gesperrt." >&2
fi
exec "$@"
