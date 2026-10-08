# Ergebnisse der Bachelorarbeit

Hier liegen die veröffentlichten Dateien der **Bachelorarbeit-Haupterhebung mit 48 geplanten Hauptläufen**. Die Auswertung gehört zum bestätigten Analysestand **`e7b04f99-3ea3-4f3f-b835-2e43c136e849`**. Die folgenden Wege dienen dem Lesen und Nachrechnen bereits erhobener Daten; dafür sind keine neuen Modellaufrufe erforderlich.

## Womit anfangen?

- **Daten direkt ansehen:** [Gemeinsame Lauftabelle `runs.csv`](Auswertung/runs.csv), dazu [Datenwörterbuch](Auswertung/data-dictionary.json) und [Methodenbeschreibung](Auswertung/methods.md). [Alle Tabellen und Diagramme](Auswertung) sowie [Einzelberichte der 48 Läufe](Einzellaeufe) sind direkt verfügbar.
- **In der Forschungsanwendung prüfen:** [Anwendung installieren](../README.md#installieren-und-starten), das unten verlinkte **Rohdaten-Studienpaket** herunterladen und über **Auswertung** importieren. Die kurze Schrittfolge steht [weiter unten](#in-der-anwendung-ansehen-und-nachrechnen).
- **Ohne Forschungsanwendung nachrechnen:** die [Anleitung des Auswertungsskripts](Auswertungsskript/README.md) verwenden. Es benötigt Python 3.13, seine festgeschriebenen Bibliotheken und dasselbe Rohdaten-Studienpaket; weder Docker noch Anwendungsdatenbank oder API-Schlüssel.

## Dateien und Archive

**Git-Clone und „Code → Download ZIP“ enthalten die Dateien im Ordner `Ergebnisse`, aber keine Release-Archive.** Die großen ZIP-Dateien separat unter [Release `abgabe-2026-10-08`](https://github.com/nofiedler/Bachelorarbeit-Forschungsumgebung-Public/releases/tag/abgabe-2026-10-08) herunterladen. Der Release gehört zu diesem Repository.

| Sammlung | Dateien / Download | Zweck |
| --- | --- | --- |
| **1. Rohdaten-Studienpaket** | [Rohdaten-ZIP herunterladen](https://github.com/nofiedler/Bachelorarbeit-Forschungsumgebung-Public/releases/download/abgabe-2026-10-08/rohdaten-e7b04f99-3ea3-4f3f-b835-2e43c136e849-41303745-67fe-4e9a-aee4-fe13932bcaee.zip) | **Primärer Prüferweg.** Enthält den bestätigten Analysestand, seine Eingaben, Tabellen und Abbildungen sowie Originaldaten und gespeicherte Revisionen der Hauptphase zum Exportzeitpunkt und verknüpfte Pilotbelege. In der Anwendung unter **Auswertung → Studienpakete übertragen und ohne Schlüssel nachrechnen** importieren. |
| **2. Auswertungsskript** | [Ordner mit eigener Anleitung](Auswertungsskript/README.md) · [Original-ZIP](https://github.com/nofiedler/Bachelorarbeit-Forschungsumgebung-Public/releases/download/abgabe-2026-10-08/auswertungsskript-e7b04f99-3ea3-4f3f-b835-2e43c136e849.zip) | Unabhängige Nachrechnung derselben gespeicherten Daten. Die 28 Dateien enthalten das Startskript, den gebundenen Rechenkern, Darstellungsquellen, Abhängigkeitenliste und Prüfmanifest. Die benötigten Bibliotheken werden nach Anleitung einmalig installiert. |
| **3. Einzelberichte aller Hauptläufe** | [Lauf01 bis Lauf48](Einzellaeufe) · [ZIP mit unveränderten Berichtsdateien](https://github.com/nofiedler/Bachelorarbeit-Forschungsumgebung-Public/releases/download/abgabe-2026-10-08/Bachelorarbeit-Rohdaten-Laeufe.zip) | Je Lauf ein CSV- und ein JSON-Bericht, insgesamt 96 Dateien. Zum direkten Lesen einzelner Messungen, Bewertungen und Herkunftsangaben. Diese Berichte ersetzen nicht die vollständigen Rohobjekte des Studienpakets. |
| **4. Laravel-Code-Sicherung** | [Laravel-Code-ZIP herunterladen](https://github.com/nofiedler/Bachelorarbeit-Forschungsumgebung-Public/releases/download/abgabe-2026-10-08/Bachelorarbeit-Haupterhebung-Laravel-Code-Sicherung.zip) | Separate lokale Prüfkopien des erzeugten Laravel-Codes der 48 Hauptläufe, einschließlich enthaltener Abhängigkeiten und Demo-Zugangsdaten. Diese sind keine Zugänge zur Forschungsinstanz oder Provider-API-Schlüssel. Zum Lesen entpacken; Hinweise zur einzelnen Prüfkopie stehen in ihrer `START.md`. Ein Start der Projekte ist für die Nachrechnung gespeicherter Messungen nicht erforderlich. |
| **5. Optionale Vollinstanzsicherung** | [Sicherungs-ZIP herunterladen](https://github.com/nofiedler/Bachelorarbeit-Forschungsumgebung-Public/releases/download/abgabe-2026-10-08/Lauf48-versuchsreihe-cb0deb44-4502-42fc-8fed-b1fdb345ff78.zip) | Historische Betriebssicherung im Format `instance-backup-v1`, Umfang `full_instance_with_separate_purposes`. Sie enthält zusätzliche Instanzbestände und 13 Phasen; sie ist nicht auf die 48 Hauptläufe begrenzt. Der dazugehörige Importweg ist **Forschung → Gesicherte Versuchsreihe importieren**. Für die Auswertungsprüfung ist dieses Archiv nicht nötig. |

Die Downloads umfassen rund **1,36 GB Rohdaten**, **1,16 GB Laravel-Code**, **1,51 GB Instanzsicherung**, **19,2 MB Einzelberichte** und **122 kB Auswertungsskript**. Die gemeinsame `analysis.json` ist rund **88,2 MB** groß. Die [komprimierte Laravel-Dateiliste](https://github.com/nofiedler/Bachelorarbeit-Forschungsumgebung-Public/releases/download/abgabe-2026-10-08/Laravel-Dateien.sha256.gz) enthält zusätzlich die Prüfsumme jeder archivierten Datei.

Der Ordner [Auswertung](Auswertung) enthält **alle 113 Dateien des unveränderten Bereichs `presentation/` aus dem Rohdaten-Studienpaket**. Dazu gehören die Tabellen, Diagramme als PDF/SVG/PNG, die zugehörigen Punktdaten und Herkunftstabellen sowie das ursprüngliche Präsentationsmanifest. So lassen sich diese Ausgaben auch ohne Installation ansehen und herunterladen.

Die gemeinsame Datei [**`analysis.json`**](Auswertung/analysis.json) ist zusätzlich als [einzelner Release-Download](https://github.com/nofiedler/Bachelorarbeit-Forschungsumgebung-Public/releases/download/abgabe-2026-10-08/analysis.json) verfügbar. Sie enthält den vollständigen **bestätigten Analysestand** einschließlich ausgewählter Messungen, Bewertungen und berechneter Vergleiche. Sie ist **keine alleinige Sammlung aller Rohobjekte oder späteren Revisionen**; dafür das Rohdaten-Studienpaket verwenden.

## In der Anwendung ansehen und nachrechnen

1. Die [Forschungsanwendung installieren und starten](../README.md#installieren-und-starten), dann **http://127.0.0.1:8000** öffnen.
2. Die **Rohdaten-ZIP aus Sammlung 1 als Datei herunterladen und nicht entpacken**. Falls der Browser Downloads automatisch entpackt, das abschalten oder die ZIP unverändert erneut speichern.
3. **Auswertung → Studienpakete übertragen und ohne Schlüssel nachrechnen → Paket importieren** öffnen, die ZIP wählen und **Paket prüfen und importieren** anklicken. Direkter Einstieg: **http://127.0.0.1:8000/packages**. Die Prüfung eines großen Archivs kann mehrere Minuten dauern.
4. Den **Importierten Analysestand** öffnen. Tabellen und Diagramme sind nach Forschungsfragen geordnet; die aufklappbaren Herkunftsangaben nennen die jeweils verwendeten Läufe. Unterhalb stehen die einzelnen Laufkarten mit Messungen, Bewertungen und Pipeline-Schritten.
5. **Gespeicherte Messungen nachrechnen** wählen. Der erwartete Erfolgsnachweis lautet **„Nachrechnung stimmt exakt mit dem bestätigten Ergebnis überein.“** Dabei werden keine Modelle und keine ursprünglichen Codeprüfläufe erneut ausgeführt.
6. Benötigte Tabellen als CSV und Abbildungen als PDF, SVG oder PNG herunterladen. Punktdaten und Herkunftsnachweise gehören jeweils zur Abbildung.

Importierte Daten bleiben von eigenen Versuchsreihen getrennt. Die bestätigte Datenauswahl und ihre historischen Ausgaben bleiben erhalten. Die installierte Darstellungsfassung kann daraus zusätzlich aktuelle Ansichten erzeugen. „Grundausstattung vorbereiten“ ist für diesen Prüferweg nicht erforderlich.

Die **optionale Vollinstanzsicherung aus Sammlung 5** gehört in den anderen Import unter **Forschung**. Dieser stellt eine getrennte Kopie her und startet keine Modelle oder Messungen automatisch. Eine Meldung über unpassende fixierte Messinstrumente betrifft die Voraussetzungen für **neue Läufe** der historischen Reihe: dafür wäre der zugehörige alte Software- und Instrumentstand erforderlich. Für das Ansehen und Nachrechnen der abgegebenen Auswertung den Studienpaketweg oben verwenden.

## Laufnummern und Dateien richtig zuordnen

Die [**Laufzuordnung `laufzuordnung.csv`**](laufzuordnung.csv) verbindet Lauf01 bis Lauf48 mit den geplanten IDs, tatsächlichen Lauf-IDs und den zugehörigen Berichtsdateien. Die UUID im Namen eines Einzelberichts bezeichnet seinen Lauf; die fortlaufende Nummer bezeichnet die Position in der geplanten Serie. Beide Kennungen sind nicht austauschbar. Zum Abgleich immer diese Zuordnung und die IDs innerhalb der Dateien verwenden.

Die Einzelberichte dokumentieren ihren jeweiligen Exportstand. Die gemeinsame Auswertung dokumentiert die im bestätigten Analysestand ausgewählten Revisionen. Das Studienpaket kann zusätzlich weitere gespeicherte Revisionen enthalten; sie gehen dadurch nicht automatisch in die bestätigte Auswertung ein.

**CSV in Excel/LibreOffice:** über **Daten → Aus Text/CSV** importieren, **UTF-8**, **Komma als Trennzeichen**, **Dezimalpunkt** wählen. IDs und Spalten mit exakten Werten (`.exact`) als Text erhalten. Leere oder unvollständige Messwerte sind keine Nullen; Status und Grund daneben beachten. Die JSON-Dateien lassen sich mit einem Text-/Codeeditor öffnen.

## Umfang und Prüfsummen

Das [Veröffentlichungsmanifest](manifest.json) beschreibt die Dateien, ihre Herkunft und den Veröffentlichungsumfang. [SHA256SUMS](SHA256SUMS) enthält die SHA-256-Prüfsummen der Release-Downloads; [DATEIEN.sha256](DATEIEN.sha256) prüft die Dateien direkt in diesem Ordner. [Die Laufzuordnung](laufzuordnung.csv) hält die Identitäten fest. Das Präsentationsmanifest unter `Auswertung/` und das Skriptmanifest unter `Auswertungsskript/` bleiben daneben als Originale erhalten.

Optionaler Dateivergleich im Terminal: im Ordner `Ergebnisse` unter macOS `shasum -a 256 -c DATEIEN.sha256` beziehungsweise unter Linux `sha256sum -c DATEIEN.sha256` ausführen. Für Downloads `SHA256SUMS` neben die heruntergeladenen Release-Dateien legen und entsprechend prüfen; nicht heruntergeladene Dateien werden als fehlend gemeldet.

Für Import und Nachrechnung die Originale unverändert aufbewahren. Eigene Tabellenformatierungen oder Berechnungen in Kopien durchführen. Alle genannten Sammlungen haben unterschiedliche Aufgaben; ein Einzelbericht, eine Code-Sicherung oder `analysis.json` allein ersetzt das importierbare Studienpaket nicht.
