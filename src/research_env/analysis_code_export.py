"""Code-only, hash-bound standalone reproduction bundle for one analysis."""
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import stat
from uuid import UUID
import zipfile

from .analysis import compute
from .analysis_presentation import PRESENTATION_FILES, presentation_binding
from .analysis_reproduction import computation_binding, computation_compatible
from .domain import canonical, digest

SOURCE_FILES = tuple(sorted(set(PRESENTATION_FILES) | {
    '__init__.py', 'analysis_resources.py', 'analysis_step_ui.py',
    'analysis_timeline_export.py', 'artifacts.py', 'locks.py', 'timing.py', 'workflow_views.py',
}))
REQUIREMENTS = frozenset({'annotated-types', 'typing-extensions', 'typing-inspection',
    'pydantic', 'pydantic-core', 'matplotlib', 'contourpy', 'cycler', 'fonttools',
    'kiwisolver', 'numpy', 'packaging', 'pillow', 'pyparsing', 'python-dateutil', 'six'})


def locked_requirements(root):
    """Retain exact versions and distribution hashes from the project's lock."""
    text = (root / 'requirements.lock').read_text()
    matches = list(re.finditer(r'^([A-Za-z0-9_.-]+)==([^\s\\]+)', text, re.MULTILINE))
    chunks, versions = [], {}
    for index, match in enumerate(matches):
        name = match.group(1).lower().replace('_', '-')
        if name not in REQUIREMENTS:
            continue
        block = text[match.start():matches[index+1].start() if index+1<len(matches) else len(text)]
        # pip's generated "via" comments may name unused application packages.
        lines = [line for line in block.splitlines() if line.strip() and not line.lstrip().startswith('#')]
        if not any('--hash=sha256:' in line for line in lines):
            raise ValueError('Paket ohne Distributionshash: ' + name)
        chunks.append('\n'.join(lines))
        versions[name] = match.group(2)
    if set(versions) != REQUIREMENTS:
        raise ValueError('Gepinnte Analyseabhängigkeiten im Lockfile unvollständig')
    return ('# Python 3.13; minimale Analyse-/Grafikabhängigkeiten, unveränderte Lockfile-Hashes.\n' +
            '\n'.join(chunks) + '\n').encode(), versions


def code_archive(snapshot, result, analysis_id):
    """Return a code-only ZIP. Never select revisions, access storage or call APIs."""
    identity = str(UUID(str(analysis_id)))
    if digest(snapshot) != result.get('data_hash'):
        raise ValueError('Analyseeingang und Ergebnis gehören nicht zusammen')
    if not computation_compatible(snapshot.get('source_binding')):
        raise ValueError('Codeexport benötigt den exakt passenden historischen Rechenkern')
    if compute(snapshot) != result:
        raise ValueError('Nachrechnung weicht vom bestätigten Ergebnis ab')
    source = Path(__file__).parent
    requirements, versions = locked_requirements(source.parents[1])
    files = {'auswerten.py': (source / 'analysis_code_runner.py').read_bytes(),
             'requirements.lock': requirements}
    for name in SOURCE_FILES:
        files['src/research_env/' + name] = (source / name).read_bytes()
    files['README.md'] = README.encode()
    manifest = {'format': 'research-analysis-code-v1', 'analysis_id': identity,
        'input_hash': result['data_hash'], 'original_result_sha256': hashlib.sha256(canonical(result).encode()).hexdigest(),
        'analysis_version': result['analysis_version'], 'historical_source_binding': snapshot['source_binding'],
        'computation_binding': computation_binding(), 'presentation_binding': presentation_binding(),
        'python': '3.13', 'platforms': ['macOS', 'Linux'], 'requirements': versions,
        'contains_research_data': False, 'contains_credentials': False,
        'scope': 'Exact historical computation plus separately versioned current presentation; no database, model calls or candidate execution.',
        'files': {name: {'sha256': hashlib.sha256(data).hexdigest(), 'byte_count': len(data)}
                  for name, data in sorted(files.items())}}
    files['manifest.json'] = canonical(manifest).encode()
    files['checksums.sha256'] = ''.join(hashlib.sha256(data).hexdigest()+'  '+name+'\n'
                                       for name, data in sorted(files.items())).encode()
    target = BytesIO()
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            entry = zipfile.ZipInfo(name)
            entry.external_attr = (stat.S_IFREG | (0o555 if name == 'auswerten.py' else 0o444)) << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, data)
    return target.getvalue()


README = '''# Auswertungsskript zum bestätigten Analysestand

Dieses ZIP enthält den tatsächlich verwendeten Rechenkern, die aktuelle separat versionierte Darstellung und einen ausführbaren Einstieg `auswerten.py`. Es enthält **keine Rohdaten und keine Zugangsschlüssel**. Das zugehörige Studienpaket muss zusätzlich heruntergeladen werden. Analyse-ID, Eingabehash, Ergebnisprüfsumme und jede Codedatei sind in `manifest.json` gebunden.

## Einmalige Vorbereitung

Benötigt werden Python **3.13** unter macOS oder Linux und die in `requirements.lock` exakt gepinnten Analyse-/Grafikbibliotheken. Windows kann eine Linux-Umgebung (z. B. WSL) verwenden. Docker und die Forschungsanwendung werden nicht benötigt.

```sh
python3.13 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r requirements.lock
```

Die Installation lädt die ausdrücklich aufgelisteten Python-Bibliotheken. Die anschließende Auswertung selbst arbeitet lokal, benötigt keine Netzwerkverbindung, keine App-Datenbank und keine Modellschlüssel. Bei bereits vorhandenen passenden Bibliotheken entfällt die Installation. Das Skript prüft ihre Versionen und installiert nichts selbst.

## Studienpaket nachrechnen

Das Code-ZIP in einen eigenen Ordner entpacken und dort ausführen:

```sh
python auswerten.py --studienpaket /Pfad/study-ANALYSE.zip --ausgabe /Pfad/nachrechnung
```

Der Ausgabeordner muss neu sein. Das Skript prüft zunächst seine eigenen Dateien, dann Struktur und sämtliche Dateiprüfsummen des Studienpakets sowie die fest gebundene Analyse-ID, den ursprünglichen Eingabehash und das bestätigte Ergebnis. Es führt keinen Code aus dem Studienpaket aus. Der gebundene Rechenkern muss das **gesamte** bestätigte Ergebnis exakt reproduzieren. Erst danach werden die Tabellen und Abbildungen erzeugt. Vorhandene Daten und Ordner werden nicht überschrieben.

Alternativ sind die unveränderten Dateien `analysis/input.json` und `analysis/analysis.json` möglich:

```sh
python auswerten.py --eingabe /Pfad/input.json --vergleich /Pfad/analysis.json --ausgabe /Pfad/nachrechnung
```

Auch dann müssen die beiden Hashes exakt passen. Ohne vollständiges Studienpaket stehen die zusätzlich benötigten Ereignisnachweise für die ergänzenden Schrittzeiten nicht zur Verfügung; diese Dateien werden dann nicht erzeugt.

## Ausgaben und wissenschaftliche Einordnung

Der neue Ausgabeordner enthält sämtliche aus dem bestätigten Ergebnis abgeleiteten CSV-Tabellen, die Abbildungen als SVG/PDF/PNG, ihre exakten Punktdaten als CSV/JSON, ein Datenwörterbuch, Rechenhinweise und einen Prüfbericht `nachrechnung.json`. Beim vollständigen Studienpaket kommen die ereignisgebundenen Pipeline-Schrittzeiten samt eigenem Quellenmanifest hinzu. Fehlwerte und bekannte Teilsummen werden weiterhin ausdrücklich gekennzeichnet.

Die mathematische Nachrechnung verwendet den **historischen** Rechenkern mit identischen Dateihashes. Tabellen- und Grafikgestaltung sind die separat im Manifest bezeichnete **aktuelle Darstellung**, keine nachträglich veränderte historische Messung oder neue Bestätigung. Historische Ausgaben bleiben im Studienpaket unter `analysis/` erhalten. Für identische CSV-/JSON-Ausgaben werden bei gleicher Darstellungsbindung die gespeicherten Dateien zusätzlich verglichen. PNG-/PDF-/SVG-Rendering kann zwischen Betriebssystemen variieren; die zugrunde liegenden Zahlen und Punktdaten werden exakt geprüft. Es werden keine neuen Messungen, Modellaufrufe, Signifikanztests oder Interpretationen hinzugefügt.

`src/research_env/` enthält unveränderte Originalmodule. Einige vollständige Hilfsmodule enthalten weitere Funktionen der Forschungsumgebung; der Einstieg ruft ausschließlich Prüfung, reine Berechnung und Dateiexport auf. Das Paket enthält weder den Webserver noch Provider-/Pipeline-Ausführung. Die ursprüngliche Anwendung muss nicht installiert sein.
'''
