"""Remove entries from selection without deleting historical scientific records."""
from datetime import datetime, timezone
from .domain import ModelPackage, ConfigurationVersion, Study, StudyPhase
from .register import GateError


def hidden(register):
    return {row[0] for row in register.connection.execute('SELECT record_id FROM catalog_visibility')}


def available(register, cls):
    removed = hidden(register)
    return [value for value in register.all(cls) if str(value.id) not in removed]


def remove(register, identity, confirmation=None):
    value = register.get(identity)
    if not isinstance(value, (ModelPackage, ConfigurationVersion, Study)):
        raise GateError('Dieser Eintrag kann nicht aus der Auswahl entfernt werden')
    if isinstance(value, ConfigurationVersion):
        phase = register.get(register._phase(value), StudyPhase)
        if phase.purpose not in ('free_test', 'demo'):
            raise GateError('Feste Forschungsbedingungen werden zusammen mit der Versuchsreihe verwaltet')
    if isinstance(value, Study):
        if confirmation != value.title:
            raise GateError('Bitte den Namen der Versuchsreihe exakt zur Bestätigung eingeben')
        phases = [str(p.id) for p in register.all(StudyPhase) if p.study_id == value.id]
        for phase in phases:
            if register.connection.execute("SELECT 1 FROM run_binding b LEFT JOIN run_completion c ON c.run_id=b.run_id WHERE b.phase_id=? AND (b.execution!='terminal' OR c.status IN ('running','retry_requested'))", (phase,)).fetchone():
                raise GateError('Die Versuchsreihe hat einen aktiven Lauf. Bitte zuerst abschließen.')
    with register.transaction():
        register.connection.execute('INSERT OR IGNORE INTO catalog_visibility VALUES (?,?)',
            (str(value.id), datetime.now(timezone.utc).isoformat()))
    return value


def require_available(register, *identities):
    removed = hidden(register)
    if any(str(identity) in removed for identity in identities):
        raise GateError('Dieses Modell wurde aus der Auswahl entfernt. Bitte ein verfügbares Modell wählen.')
