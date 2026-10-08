"""Readable study dashboard; unchanged calculations and immutable analysis outputs."""
from fractions import Fraction
import json
from uuid import UUID
from .domain import Study, StudyPhase, Freeze, AnalysisRun, TimeInterval
from .timing import summarize
from .adapter import CallJournal
from .catalog_ui import available
from .register import GateError
from .preparation_views import runs
from .workflow_views import results


def metric(value):
    """Human display only; exact fractions remain in all exports."""
    if isinstance(value,dict):value=value.get('value')
    if value is None:return 'Offen'
    try:return f'{float(Fraction(str(value))):.4g}'.replace('.',',')
    except (ValueError,ZeroDivisionError):return str(value)


def timestamp(value):
    if not value:return '—'
    from datetime import datetime
    return datetime.fromisoformat(value).strftime('%d.%m.%Y %H:%M:%S')


def overview(register, study_id=None, page=1, *, include_results=True):
    phases=[p for p in register.all(StudyPhase) if p.purpose=='main']
    studies=[s for s in available(register,Study) if any(p.study_id==s.id for p in phases)]
    studies.reverse()
    selected=next((s for s in studies if str(s.id)==study_id),None) if study_id else next(iter(studies),None)
    if study_id and selected is None:raise GateError('Versuchsreihe ist nicht in der aktuellen Auswahl')
    phases=[p for p in phases if selected and p.study_id==selected.id]
    pids={p.id for p in phases}
    freezes=[(f,next(p for p in phases if p.id==f.phase_id)) for f in register.all(Freeze) if f.phase_id in pids]
    analyses=[a for a in register.all(AnalysisRun) if a.phase_id in pids]
    proposals=[dict(p) for p in register.connection.execute('SELECT * FROM analysis_selection ORDER BY rowid DESC')
        if str(p['phase_id']) in {str(x) for x in pids}]
    run_rows=[]
    if selected and include_results:
        run_rows=[row for row in runs(register,{})['rows'] if row['phase'] in {str(x) for x in pids}]
    total=len(run_rows);pages=max(1,(total+9)//10);page=min(max(1,int(page)),pages)
    run_rows=run_rows[(page-1)*10:page*10]
    for row in run_rows:
        row['results']=results(register,row['run'].id) if row['run'] else None
        if row['run']:
            run=row['run'];binding=register.connection.execute('SELECT clock_json FROM pipeline_binding WHERE run_id=?',(str(run.id),)).fetchone()
            intervals=tuple(register.get(r[0],TimeInterval) for r in register.connection.execute("SELECT id FROM register_record WHERE kind='TimeInterval' AND json_extract(payload,'$.run_id')=?",(str(run.id),)))
            row['time']=summarize(intervals,coverage_complete=register.state(run.id).execution=='terminal' and bool(binding) and binding['clock_json'] is None and bool(intervals))['active'].model_dump(mode='json')
            row['cost']=CallJournal.cost_view(register).effective_costs(run.id)
    return {'studies':studies,'selected_study':selected,'freezes':freezes,'analyses':analyses,'proposals':proposals,
        'result_rows':run_rows,'page':page,'pages':pages,'total':total}

def report_number(value, scale=1):
    """Display conversion only; never alter saved observations or analysis outputs."""
    if isinstance(value, dict):
        value = value.get('value')
    if value is None:
        return 'Nicht berechenbar'
    number = Fraction(str(value)) * scale
    places = 2 if abs(number) >= 1 or scale != 1 else 6
    rendered = f'{float(number):,.{places}f}'.rstrip('0').rstrip('.')
    return rendered.replace(',', '\u202f').replace('.', ',')


def metric_label(key):
    labels = {
        'F': 'Funktionaler Score F', 'D': 'PHPStan-Diagnosen D', 'L': 'PHP-Codezeilen L',
        'S': 'Diagnosen je 100 Codezeilen S', 'T': 'Zielkonformität T',
        'full_success': 'Vollständiger Erfolg (F = 1)', 'raw_pass_ratio': 'Roh-Testpassquote',
        'pipeline_seconds': 'Aktive Pipelinezeit', 'setup_seconds': 'Einrichtungszeit',
        'evaluation_seconds': 'Unabhängige Prüfzeit', 'manual_review_seconds': 'Manuelle Bewertungszeit',
        'context_preparation_seconds': 'Kontextvorbereitung', 'call_count': 'Modellaufrufe',
        'transport_count': 'Transportversuche', 'repair_count': 'Repair-Schritte',
        'tokens.input': 'Eingabetokens', 'tokens.output': 'Ausgabetokens',
        'tokens.total': 'Tokens gesamt', 'tokens.reasoning': 'Reasoning-Tokens',
        'tokens.cache_read': 'Aus dem Cache gelesene Tokens',
        'tokens.cache_write': 'In den Cache geschriebene Tokens', 'tokens.other': 'Weitere Tokens',
    }
    return labels.get(key, 'Kosten (' + key[5:] + ')' if key.startswith('cost:') else key)


def unit_label(unit):
    return {'s': 's', 'diagnoses': 'Diagnosen', 'lines': 'Zeilen',
        'diagnoses_per_100_lines': 'Diagnosen / 100 Zeilen', 'count': 'Anzahl',
        'tokens': 'Tokens', 'ratio': 'Score 0–1', 'binary': 'Anteil 0–1'}.get(unit, unit)


def plot_help(name):
    """Reading guide for the archived plots, without regenerating historic figures."""
    guides = {
        'kontext-verteilungen': ('Funktionale Ergebnisse nach Kontext',
            'Vergleiche K0 und K1 innerhalb desselben Moduls. Jeder Punkt ist ein Lauf; höheres F bedeutet mehr erfüllte Anforderungen bei erfülltem Zielauftrag. Die Verteilung zeigt zugleich die Wiederholungsstreuung (UF1, UF2, UF6). Einzelne Gruppen können verschieden viele gültige Werte haben; der gepaarte Hauptvergleich steht oben.'),
        'blockunterschiede': ('Kontextunterschiede in Blockreihenfolge',
            'Jeder Punkt ist der mittlere K1−K0-Unterschied eines vollständigen Kernblocks. Oberhalb von null liegt K1 vorn, unterhalb K0. Wechselnde Höhen zeigen Schwankung; eine erkennbare zeitliche Richtung ist ein Anlass, Ereignislogs zu prüfen, aber kein Beweis für Drift (UF2, UF6). Fehlende Blöcke sind keine Nullwerte.'),
        'modellzuordnung': ('Welches Modell produziert und prüft?',
            'P bezeichnet das produzierende, V das prüfende Modell. Die erste Gruppe SQL Injection / K1 ist die gemeinsame A/A-Referenz. Vergleiche die vier Gruppen nur im SQL/K1-Fokusfall; die zugehörigen gepaarten Unterschiede stehen unter Modellzuordnung (UF3).'),
        'pipeline-varianten': ('Was tragen Planner und Review bei?',
            'Die erste Gruppe SQL Injection / K1 verwendet Planner und Review gemeinsam. Die übrigen Gruppen lassen jeweils einen oder beide Schritte aus. Höhere F-Werte sind bessere funktionale Ergebnisse; ob sich die Beiträge gegenseitig verändern, beschreibt der Interaktionsvergleich (UF4).'),
        'statisch-D': ('Wie viele PHPStan-Diagnosen treten auf?',
            'Hier stehen absolute Diagnosezahlen je Lauf, keine K1−K0-Differenzen. Weniger Meldungen sind im verwendeten Regelwerk günstiger. Null Meldungen beweisen weder Fehlerfreiheit noch Sicherheit. Vergleiche zusätzlich Codeumfang L, Diagnosedichte S und funktionalen Score F (UF1).'),
        'statisch-L': ('Wie viel PHP-Code wird analysiert?',
            'L ist der betrachtete Codeumfang je Lauf. Mehr Zeilen sind für sich weder besser noch schlechter. Der Umfang hilft einzuschätzen, ob eine unterschiedliche Diagnosezahl auch mit unterschiedlich viel analysiertem Code zusammenhängt (UF1).'),
        'statisch-S': ('Diagnosen relativ zum Codeumfang',
            'S = 100 × D / L gibt die Diagnosen je 100 analysierte PHP-Zeilen an. Eine geringere Dichte ist nur für diese statische Analyse günstiger. Bei L = 0 ist S nicht definiert und fehlt im Diagramm. D, L und F gehören zur Interpretation dazu (UF1).'),
        'F-zeit': ('Funktionales Ergebnis und Zeitaufwand',
            'Jeder Punkt verbindet F mit der aktiven Pipelinezeit desselben Kernlaufs. Weiter oben bedeutet mehr funktionale Erfüllung, weiter links weniger Zeit. Gezeigt werden nur Läufe mit beiden Werten; Setup, Kontextaufbereitung und externe Prüfung sind gesonderte Aufwände (UF5).'),
    }
    if name.startswith('F-kosten-'):
        return ('Funktionales Ergebnis und API-Kosten',
            'Jeder Punkt verbindet F mit vollständig bekannten API-Kosten desselben Kernlaufs. Weiter oben bedeutet mehr funktionale Erfüllung, weiter links weniger Kosten. Währungen bleiben getrennt. Eine leere Grafik bedeutet fehlende vollständige Wertepaarungen, nicht kostenlose Läufe. Aufwände fehlgeschlagener Läufe bleiben in den Ressourcenwerten erhalten (UF5).')
    return guides.get(name, (name, 'Diese Grafik gehört zum gespeicherten Analysestand. Die zugehörigen Einzelwerte stehen in den Laufdaten.'))
