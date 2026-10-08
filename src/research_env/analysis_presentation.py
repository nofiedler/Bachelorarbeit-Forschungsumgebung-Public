"""Versioned, reproducible presentation derived from a confirmed result.

This cache is disposable. It never writes an Artifact, AnalysisRun or selected
revision, and never changes the source binding inside a historical snapshot.
"""
import csv
import hashlib
from io import StringIO
import json
from pathlib import Path

from .artifacts import IntegrityError
from .domain import canonical, digest

PRESENTATION_VERSION = 'research-report-v2-guided'
PRESENTATION_FILES = (
    'analysis_export.py', 'analysis_tables.py', 'analysis_report_tables.py',
    'analysis_charts.py', 'analysis_report_data.py', 'analysis_presentation.py',
    'analysis_population.py', 'analysis_table_population.py',
    'analysis.py', 'analysis_metrics.py', 'analysis_reproduction.py', 'numeric.py',
    'domain.py', 'matrix.py', 'analysis_reporting_plan.json',
)


def presentation_binding():
    root = Path(__file__).parent
    return {'version': PRESENTATION_VERSION,
            'files': {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                      for name in PRESENTATION_FILES if (root / name).is_file()}}


METRICS = {
    'F': {'unit': 'ratio', 'definition': 'T × (R1 + R2 + R3 + R4 + R5 + R6) / 6; R-Kategorien erhalten gleiches Gewicht.'},
    'T': {'unit': 'binary', 'definition': 'Laravel-Zielkonformität aus T1–T5; fünf erfüllt = 1, fachlich verfehlt = 0; offene oder technisch fehlende Urteile bleiben gekennzeichnet.'},
    'R1-R6': {'unit': 'binary', 'definition': 'Eine Anforderungskategorie ist 1, wenn alle zugeordneten unabhängigen Testfälle bestanden sind; bei einer fachlichen Verfehlung 0. Technisch fehlende Nachweise bleiben fehlend.'},
    'T1-T5': {'unit': 'binary', 'definition': 'Einzeln ausgewählte, abgeschlossene Bewertungen mit Revisions- und Nachweisbezug.'},
    'D': {'unit': 'diagnoses', 'definition': 'PHPStan-/Larastan-Diagnosen im festgelegten Analyseumfang.'},
    'L': {'unit': 'lines', 'definition': 'Physische PHP-Codezeilen im Analyseumfang; Kommentare, Leerzeilen und PHP-Tags zählen nicht.'},
    'S': {'unit': 'diagnoses_per_100_lines', 'definition': '100 × D / L; bei L = 0 nicht definiert. D, L und der Gültigkeitsstatus werden zusätzlich berichtet.'},
    'pipeline_seconds': {'unit': 's', 'definition': 'Gemessene aktive Pipelineintervalle vom ersten Rollenaufruf bis zur Versiegelung; tatsächliche Pausen und Ausfallzeit ausgeschlossen. Unvollständige Erfassung ist kein gültiger Gesamtwert.'},
    'tokens': {'unit': 'tokens', 'definition': 'Dokumentierte Anbieterwerte; Gesamt-, Reasoning- und Cachekategorien können sich überschneiden und werden nicht addiert.'},
    'cost': {'unit': 'currency field', 'definition': 'Dokumentierte API-Kosten; Währungen getrennt. Bekannte Teilsummen ersetzen keinen vollständigen Kostenwert.'},
}


def field_description(field):
    from .analysis_report_tables import FIELD_DESCRIPTIONS
    if field in FIELD_DESCRIPTIONS:
        return FIELD_DESCRIPTIONS[field]
    suffixes = {
        '.exact': 'Exakter gespeicherter Zahlenwert, gegebenenfalls als rationaler Bruch.',
        '.status': 'Beobachtungsstatus; fehlende oder unvollständige Werte sind keine Null.',
        '.unit': 'Einheit dieses Wertes.', '.reason': 'Dokumentierter Grund für den Zustand bzw. Fehlwert.',
        '.known_subtotal': 'Bekannte Teilsumme; bei unvollständiger Messung kein vollständiger Gesamtwert.',
    }
    for suffix, text in suffixes.items():
        if field.endswith(suffix):
            return text
    exact = {
        'planned_id': 'Unveränderliche ID der geplanten Matrixposition.',
        'run_id': 'ID des tatsächlich gestarteten Laufs; leer bei nicht gestartetem Planplatz.',
        'candidate_hash': 'SHA-256 des versiegelten Kandidaten, soweit vorhanden.',
        'configuration_id': 'ID der eingefrorenen Konfiguration.',
        'configuration_hash': 'Hash der eingefrorenen Konfiguration.',
        'block_id': 'ID des Wiederholungsblocks.', 'block_index': 'Ordnungsnummer des Wiederholungsblocks.',
        'position': 'Fixierte Position in der Laufreihenfolge.', 'cell_key': 'Kennung der Versuchsbedingung.',
        'data_hash': 'Hash des bestätigten, unveränderten Analyseeingangs.',
        'analysis_version': 'Version der bestätigten arithmetischen Analyse.',
        'table_unit': 'Allgemeine Einheitenangabe; spezifische unit-Spalten haben Vorrang.',
        'denominator_sets': 'JSON mit den im Export bezeichneten ID-Mengen; kennzahlspezifische gültige IDs/Nenner haben Vorrang.',
        'n': 'Tatsächlich gültige Beobachtungen für genau diese Zeile und Kennzahl.',
        'planned_n': 'Geplante Beobachtungen für genau diese Zeile.',
        'ids': 'IDs der tatsächlich einbezogenen Beobachtungen als JSON-Liste.',
        'group_key': 'Eindeutige Teilgruppe der Diagrammherkunft; Gruppen dürfen verschiedene Bezugs- und Beobachtungseinheiten haben.',
        'group_label': 'Lesbare Bezeichnung der dargestellten Teilgruppe.',
        'role': 'Rolle innerhalb genau dieser Teilgruppe: used = beobachteter Wert verwendet; not_used = nicht verwendet; omitted = bewusst weggelassen; bounded_missing = nur rechnerische Fehlwertgrenze.',
        'used_as_observation': 'Wahr nur bei tatsächlich verwendetem beobachtetem Laufwert in dieser Teilgruppe; Fehlwertgrenzen sind keine Beobachtungen.',
        'group_observation_unit': 'Einheit der Teilgruppe: Lauf, vollständiges Modulpaar, Kernblock, Vierergruppe, verbleibender Kernblock oder geplantes Modulpaar für Fehlwertgrenzen.',
        'group_observation_count': 'Zahl der Einheiten dieser Teilgruppe, nicht zwangsläufig Zahl der Läufe; bei Fehlwertgrenzen der geplante Paarnenner.',
        'group_planned_run_count': 'Zahl verschiedener geplanter Laufpositionen in der Bezugsmenge dieser Teilgruppe.',
        'group_used_run_count': 'Zahl verschiedener tatsächlicher Lauf-IDs mit verwendetem beobachtetem Wert innerhalb dieser Teilgruppe.',
        'evidence_ids': 'IDs der unveränderten Nachweise als JSON-Liste.',
        'unit': 'Einheit der Kennzahl.', 'status': 'Dokumentierter Zustand; nicht gleichbedeutend mit bestanden.',
        'value': 'Zahlenwert; leer bedeutet fehlend und nicht null.',
        'step_index': 'Reihenfolge des Pipeline-Schritts (1–9); 10–12 kennzeichnen getrennte unabhängige Prüfungen.',
        'model_step': 'Kennzeichnet einen Schritt mit Modellaufrufen; interne Werkzeuge und Versiegelung sind keine Modellschritte.',
        'call_ids': 'IDs der dokumentierten Modellaufrufe dieses Schritts als JSON-Liste.',
        'calls': 'Anzahl dokumentierter Modellaufrufe dieses Schritts.',
        'transports': 'Anzahl dokumentierter Transportversuche der Modellaufrufe dieses Schritts.',
        'event_time.value': 'Summe vollständiger Start-/Endereignisspannen desselben Prozesses in Sekunden; kein Ersatz für monotone Pipelinezeit.',
        'event_time.event_ids': 'IDs der bis zum gespeicherten Stichtag verwendeten Start-/Endereignisse; Hashes stehen im Schrittzeitmanifest.',
    }
    return exact.get(field, 'Feld aus dem bestätigten Ergebnis bzw. seiner dokumentierten tabellarischen Ableitung; Punktnotation bezeichnet verschachtelte Felder.')


def data_dictionary(result, files):
    from .analysis_report_tables import TABLE_DESCRIPTIONS
    tables = {}
    manifest = json.loads(files['manifest.json'][0])
    for name, (content, _) in files.items():
        if not name.endswith('.csv'):
            continue
        # Field names cannot contain embedded newlines. Only decode the header,
        # not huge raw-report JSON cells, which may exceed csv.field_size_limit.
        reader = csv.DictReader(StringIO(content.split(b'\n', 1)[0].decode('utf-8')))
        fields = reader.fieldnames or []
        row_count = manifest['files'][name]['row_count']
        tables[name] = {'row_count': row_count,
                       'description': ('Diagrammherkunft: eine Zeile je geplanter Laufposition und Teilgruppe. '
                           'Derselbe Lauf kann in mehreren Gruppen vorkommen; Zeilen und Gruppenzahlen sind kein gemeinsamer statistischer Nenner.'
                           if name.endswith('-population.csv') else TABLE_DESCRIPTIONS.get(name.removesuffix('.csv'),
                           'Daten des bestätigten Analysestands bzw. separat gebundene Punkt-/Ereignisdaten.')), 'columns': [
            {'name': field, 'description': field_description(field),
             'encoding': 'UTF-8 text; decimal point; empty = missing; nested lists/objects = canonical JSON'}
            for field in fields]}
    return {'schema': 'research-report-data-dictionary-v1', 'data_hash': result['data_hash'],
            'analysis_version': result['analysis_version'], 'presentation_version': PRESENTATION_VERSION,
            'csv': {'encoding': 'UTF-8', 'delimiter': ',', 'decimal_separator': '.',
                    'line_ending': 'LF', 'missing': 'empty field; inspect associated status/reason',
                    'exact_values': 'Rational strings in exact columns; decimal display does not replace the exact value.',
                    'empty_table': 'A metadata-only row with n=0 is not an observation.'},
            'metrics': METRICS, 'tables': tables,
            'provenance': 'Join planned_id/run_id/configuration_id/candidate_hash/evidence_ids with the package register and originals. No inference or interpretation is encoded.'}


METHODS = '''# Rechen- und Lesehinweise

Diese Darstellung leitet Tabellen und Abbildungen aus einem bestätigten Analysestand ab. Sie wählt keine neueren Messungen oder Bewertungen aus. Die historische Eingabe und das bestätigte Ergebnis bleiben unverändert. Jede Datei ist über `manifest.json` an den Datenhash und die Präsentationsquellen gebunden.

## Reihenfolge und Forschungsbezug

1. Datenabdeckung: zuerst prüfen, für wie viele geplante Läufe tatsächlich verwendbare Werte vorliegen. Fehlende Werte sind keine Nullwerte. Ein vorhandener Nachweis ist nicht automatisch ein bestandenes Ergebnis.
2. UF2 / H-K – Was ändert sich mit dem Kontext? Verglichen werden K0 und K1 desselben Moduls im selben Wiederholungsblock, bei A/A und vollständiger Pipeline. Einzelpunkte zeigen die Ergebnisse der Läufe; Verbindungslinien verbinden die beiden Läufe eines solchen Vergleichspaars. Der geplante Gesamtvergleich gewichtet die drei Module gleich: d_mb = F(K1) − F(K0), g_b = (d_BF,b + d_SQL,b + d_UP,b) / 3, Δ_C = Mittelwert der g_b. Die Menge B_C enthält nur Blöcke mit allen sechs gültigen Kernwerten. 100 × Δ_C wird in Prozentpunkten angegeben. D, L und S beschreiben ergänzend die statische Codeanalyse, nicht die funktionale Korrektheit.
3. UF1 – Unterscheidet sich die Kontextdifferenz je Modul? Die drei Modulprofile und die einzelnen Paarunterschiede machen sichtbar, welche Werte in den Gesamtvergleich eingehen. Vergleichbare Modulmittel verwenden dieselbe vollständige Blockmenge B_C. Ergänzende Paarmittel über alle gültigen Paare eines Moduls haben eigene Nenner und ersetzen den primären Kontrast nicht. Unterschiede zwischen Modulkontrasten beschreiben diese drei Aufgaben, keine allgemeine Rangfolge der Modulschwierigkeit.
4. UF5 – Wie viel Aufwand gehört zu den Ergebnissen? Pipelinezeit, Tokens und API-Kosten werden getrennt berichtet. Im Streudiagramm steht jeder Punkt für Aufwand und funktionales Ergebnis desselben Laufs; Farben und Marker kennzeichnen Kontext und Modul. Kontextdifferenzen vergleichen wiederum K1 und K0 desselben Moduls/Blocks. Fehlende Gesamtwerte dürfen nicht durch bekannte Teilsummen ersetzt werden. Ohne vollständig erhobene Setup-, Kontext- und Bewertungszeiten ist keine Gesamtwirtschaftlichkeit abgebildet; es wird kein gemeinsamer Nutzen- oder Wirtschaftlichkeitswert berechnet.
5. UF6 – Wie unterschiedlich fallen Wiederholungen aus? Verglichen werden einzelne Läufe derselben vollständigen Konfiguration. Die Tabelle enthält n, Mittelwert, Median, Minimum, Maximum und Stichprobenstandardabweichung s (Nenner n − 1; nur n ≥ 2). Die Grafik zeigt alle Einzelwerte; seitlicher Versatz trennt überlagerte Punkte und hat keine inhaltliche Bedeutung. Median und Spannweite ergänzen die Punkte. Bei wenigen Wiederholungen bleiben einzelne Läufe wichtiger als eine zusammenfassende Verteilungsform. Die Streuung der Paarunterschiede und Blockwerte wird getrennt von der Streuung der Laufwerte berichtet. Wiederholungen sind keine unabhängigen Softwareprojekte.
6. UF3 – Was ändert sich bei der Modellzuordnung? Ergänzend wird ausschließlich SQL Injection bei K1 und vollständiger Pipeline betrachtet. Die Zuordnungen A/A, A/B, B/A und B/B gehören zum selben Zusatzblock. V-Kontraste AB − AA und BA − BB halten den Produzenten fest; Produzentenkontraste BA − AA und BB − AB halten die Verifikation fest. Vollständige Quartette haben eine eigene Blockmenge und einen eigenen Nenner. Die A/A-Referenz ist ein bereits gezählter Kernlauf und keine zusätzliche unabhängige Migration.
7. UF4 – Was ändert sich ohne Planner beziehungsweise Review? Ergänzend werden im SQL-Fokusfall bei K1 und A/A die Varianten 11, 01, 10 und 00 verglichen (erste Ziffer: Planner; zweite: Review). Die bedingten Unterschiede 11 − 01, 10 − 00, 11 − 10 und 01 − 00 zeigen jeweils den Vergleich bei festgehaltenem anderen Baustein. Der Interaktionskontrast lautet 11 − 01 − 10 + 00. Weglassen verändert auch die bereitgestellte Information und den Ressourcenbedarf; es ist kein Vergleich bei gleichem Budget.

Die Reihenfolge UF2 → UF1 → UF5 → UF6 → UF3 → UF4 folgt der Forschungsfragendatei. F1 führt Kontextwirkung, Modulunterschiede, Aufwand und Streuung zusammen, ohne einen weiteren Gesamtwert zu bilden. UF3 und UF4 ergänzen diesen Kern im festgelegten Fokusfall. Die Ausgaben zeigen Daten und Rechenregeln; die inhaltliche Einordnung erfolgt im Manuskript.

## Einzelwerte, Paare und Blöcke auseinanderhalten

Ein Lauf ist eine Migration unter einer vollständigen Konfiguration. Ein Paar besteht aus einem K0- und einem K1-Lauf desselben Moduls und Blocks. Ein Kernblock umfasst drei solche Paare, also sechs Läufe. Ein Punkt kann je nach ausdrücklich benannter Abbildung einen Laufwert, einen Paarunterschied oder den Mittelwert der drei Paarunterschiede darstellen. Die Diagrammdaten nennen diese Einheit und die verwendeten IDs.

Die Blockzusammenfassung ist für den vorab festgelegten primären Vergleich notwendig. Sie ersetzt nicht die Einzelwerte. Deshalb werden zuerst die Laufwerte und danach die daraus berechneten Unterschiede dargestellt. Ein Boxplot ist nicht erforderlich: Bei drei beziehungsweise fünf Wiederholungen und diskreten F-Werten können reine Boxen Beobachtungen verdecken. Einzelpunkte mit Median und Spannweite erfüllen den vorgesehenen deskriptiven Zweck, ohne eine Verteilungsform zu unterstellen. Diese Darstellungsänderung verändert weder den Auswertungsplan noch die gespeicherten Werte.

## Sensitivität und Fehlwerte

Die Weglassanalyse berechnet den primären Mittelwert ohne jeweils einen vollständigen Block; bei n_C < 2 ist sie nicht definiert. Ihre Spannweite ist kein Konfidenzintervall. Konservative Fehlwertgrenzen setzen unbekannte F-Werte jeweils innerhalb [0,1] so an, dass die kleinste bzw. größte Kontextdifferenz entsteht; Nenner ist die geplante Anzahl 3 × r_C der Modulpaare. Technische Messlücken bleiben von fachlich verfehlten Kriterien getrennt. Kennzahlen können unterschiedliche gültige ID-Mengen haben. Es werden weder p-Werte noch Bootstrap- oder Konfidenzintervalle ergänzt.

## Tabellen und Abbildungen verwenden

`runs.csv` verknüpft geplante Laufpositionen, tatsächliche Lauf-IDs, Konfiguration und Kandidatenhashes. Detailtabellen enthalten Testfälle, Assertions, ausgewählte Bewertungen, Zeitintervalle und Modellaufrufe. CSV-Zahlen nutzen einen Dezimalpunkt. Exakte Brüche und Statusspalten bleiben erhalten; Leerfelder sind keine Nullen. Komplexe Listen sind kanonisches JSON. `data-dictionary.json` enthält jede exportierte Spalte, Einheiten und das Datenschema.

Die ergänzende Tabelle `pipeline-steps.csv` (mit gleichnamigem JSON und eigenem Manifest) enthält je geplantem Lauf die neun Pipeline-Schritte und drei unabhängigen Prüfungen. Schrittzeiten stammen aus Start-/Endereignissen desselben Prozesses bis zum Erstellzeitpunkt des bestätigten Eingabeartefakts. Das eigene Manifest bindet diesen Stichtag, die Eingabedatei und jedes verwendete Ereignis über IDs und Hashes. Diese Ereigniszeiten sind weder reine Modell-Inferenzzeit noch Ersatz für die separat gemessene monotone aktive Pipelinezeit.

Abbildungen stehen als SVG/PDF (Vektor) und PNG zur Verfügung. Die jeweiligen `*-data.csv` und `*-data.json` enthalten die tatsächlich dargestellten Punkte und Nenner; `plot-data.json` ergänzt die laufübergreifenden Ressourcen-/Ergebniswerte. Farben sind durch Beschriftungen oder Marker ergänzt. Angaben zu n beziehen sich auf gültige Beobachtungen der jeweiligen Grafik, nicht automatisch auf alle geplanten Läufe. Diese Dateien berichten Daten und Rechenregeln; sie enthalten keine inhaltliche Interpretation der Werte.

Zu jeder Abbildung enthält `*-population.csv` die Herkunft je geplanter Laufposition und Teilgruppe: Laufnummer, Plan-ID, tatsächliche Lauf-ID, Konfiguration, Block und Verwendungsstatus. Dieselbe Herkunft steht strukturiert unter `population` in den Diagramm-JSON-Dateien und im Manifest. Einzelpunkte, vollständige Paare, Kernblockmittel und vollständige Zusatzquartette haben eigene Gruppen. Die Sensitivitätsgrafik unterscheidet den primären Bezug, jede konkrete Weglassrechnung und die geplante Bezugsmenge der Fehlwertgrenzen. Eine bei der Grenzberechnung berücksichtigte fehlende Beobachtung ist kein gemessener Laufwert. Die Vereinigung der Lauf-IDs ist nur ein Herkunftsverzeichnis und kein gemeinsamer statistischer Nenner; dieselben Läufe können in mehreren Gruppen vorkommen. Zeilen- und Gruppenzahlen dürfen deshalb nicht addiert werden.

## Reproduzierbarkeit

Ein Studienpaket hält `analysis/input.json`, `analysis/analysis.json`, die Bestätigung und historische Ausgaben unverändert vor. `presentation/` ist eine separat versionierte Ableitung aus genau diesem Ergebnis. Eine Nachrechnung verwendet ausschließlich die installierte, in allen Abhängigkeiten exakt passende Rechenfunktion. Sie muss das gesamte historische Ergebnis identisch erzeugen. Abweichende Auswahl- oder Präsentationsdateien ändern weder die eingefrorene Eingabe noch ihre ursprüngliche Quellenbindung. Mitgelieferter Archivcode wird nicht automatisch ausgeführt. Für die Erhebung neuer Daten oder eine neue Auswahl gelten weiterhin deren eigene Versions- und Bestätigungsprüfungen.
'''


def presentation_files(result, *, original_result_sha256=None):
    from .analysis_export import render
    files = render(result)
    binding = presentation_binding()
    result_sha = original_result_sha256 or hashlib.sha256(canonical(result).encode()).hexdigest()
    files['data-dictionary.json'] = (canonical(data_dictionary(result, files)).encode(), 'application/json')
    files['methods.md'] = (METHODS.encode(), 'text/markdown; charset=utf-8')
    plan = Path(__file__).with_name('analysis_reporting_plan.json')
    if plan.is_file():
        files['reporting-plan.json'] = (plan.read_bytes(), 'application/json')
    files['README.md'] = ((
        '# Tabellen und Abbildungen zum bestätigten Analysestand\n\n'
        f'Datenhash: `{result["data_hash"]}`\n\n'
        f'Analyse: `{result["analysis_version"]}` · Darstellung: `{PRESENTATION_VERSION}`\n\n'
        '`methods.md` erläutert Zuordnung zu Forschungsfragen, Rechenregeln, Nenner und Grenzen. '
        '`data-dictionary.json` beschreibt CSV-Formate, Spalten und Kennzahlen. '
        '`manifest.json` enthält Quellenbindung und SHA-256 jeder Ausgabe. '
        'Das zugrunde liegende bestätigte Ergebnis liegt unverändert in `analysis.json`. '
        'Die historischen Ausgaben des Studienpakets bleiben separat unter `analysis/` erhalten.\n'
    ).encode(), 'text/markdown; charset=utf-8')
    manifest = json.loads(files['manifest.json'][0])
    manifest.update(presentation_version=PRESENTATION_VERSION, presentation_binding=binding,
                    original_result_sha256=result_sha,
                    derivation='Display/export only from the unchanged confirmed result; no selection or scientific approval')
    for name, (content, mime) in files.items():
        if name == 'manifest.json':
            continue
        item = manifest['files'].setdefault(name, {'data_hash': result['data_hash'],
            'analysis_version': result['analysis_version'], 'unit': 'not applicable', 'denominator_sets': {}})
        item.update(sha256=hashlib.sha256(content).hexdigest(), byte_count=len(content), mime_type=mime)
    files['manifest.json'] = (canonical(manifest).encode(), 'application/json')
    return files


def validate_cache(directory):
    manifest = json.loads((directory / 'manifest.json').read_bytes())
    for name, item in manifest['files'].items():
        path = directory / name
        if path.is_symlink() or path.parent != directory or not path.is_file():
            raise IntegrityError('Darstellungscache enthält einen ungültigen Dateipfad')
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != item['sha256']:
            raise IntegrityError('Darstellungscache verändert; historische Daten bleiben unverändert')
    return manifest


def presentation_cache(settings, result):
    """Return a validated disposable directory without mutating the register."""
    import fcntl
    import os
    import shutil
    import tempfile
    root = settings.staging / 'analysis-presentations'
    root.mkdir(parents=True, exist_ok=True)
    result_sha = hashlib.sha256(canonical(result).encode()).hexdigest()
    binding = presentation_binding()
    key = digest({'original_result_sha256': result_sha, 'presentation': binding})
    destination = root / key
    with (root / (key + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not destination.exists():
            temporary = Path(tempfile.mkdtemp(prefix='.' + key + '-', dir=root))
            try:
                for name, (content, _) in presentation_files(result, original_result_sha256=result_sha).items():
                    (temporary / name).write_bytes(content)
                validate_cache(temporary)
                os.rename(temporary, destination)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        # The publishing pass verifies every byte. Downloads verify their own
        # file; HTML refreshes must not re-read every large raw-data CSV/image.
        manifest = json.loads((destination / 'manifest.json').read_bytes())
        if (manifest.get('presentation_binding') != binding or
                manifest.get('original_result_sha256') != result_sha or
                manifest.get('data_hash') != result['data_hash']):
            raise IntegrityError('Darstellungscache passt nicht zum bestätigten Ergebnis')
    return destination


def cached_presentation_file(settings, key, filename, *, data_hash):
    """Validate one hash-addressed download without reading a research result."""
    import re
    if not re.fullmatch(r'[a-f0-9]{64}', key) or not re.fullmatch(r'[A-Za-z0-9_-]+\.(csv|json|png|svg|pdf|md)', filename):
        raise IntegrityError('Unbekannte Darstellungsdatei')
    directory = settings.staging / 'analysis-presentations' / key
    manifest_path = directory / 'manifest.json'
    if directory.is_symlink() or manifest_path.is_symlink() or not manifest_path.is_file():
        raise IntegrityError('Darstellungscache fehlt; Analyseansicht erneut öffnen')
    manifest = json.loads(manifest_path.read_bytes())
    expected_key = digest({'original_result_sha256': manifest.get('original_result_sha256'),
                           'presentation': manifest.get('presentation_binding')})
    if manifest.get('data_hash') != data_hash or key != expected_key:
        raise IntegrityError('Darstellungsdatei gehört nicht zum bestätigten Datenstand')
    if filename == 'manifest.json':
        return manifest_path, 'application/json'
    item = manifest['files'].get(filename)
    path = directory / filename
    if not item or path.is_symlink() or not path.is_file():
        raise IntegrityError('Unbekannte Darstellungsdatei')
    with path.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual != item['sha256']:
        raise IntegrityError('Darstellungsdatei wurde verändert')
    return path, item['mime_type']
