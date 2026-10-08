# Auswertungsskript zum bestätigten Analysestand

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
