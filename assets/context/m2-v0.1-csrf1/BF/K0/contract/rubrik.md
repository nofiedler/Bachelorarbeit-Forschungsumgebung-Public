# R-/T-Rubrik RT-v0.1

**Entwurf, menschliche Fachprüfung offen; gehört zu M2-v0.1.** Grundlage: Operationalisierung §§2.3, 2.4, 6.1 ([Original](https://github.com/nofiedler/Bachelorarbeit-Dokumentation/blob/42cf379b5588e7eff939bbf063567836bb3d4cb3/005-Forschungsablauf/01-Operationalisierung-und-Auswertungsplan.md)), konkretisiert durch [Issue #3](https://github.com/nofiedler/Bachelorarbeit-Forschungsumgebung/issues/3) und [Plan §7.2](../../planung/02-Technischer-Implementierungsplan.md#72-funktionale-prüfung-und-laravel-struktur). HTTP-, DOM-, Daten- und Speicherregeln stehen vollständig im [Vertrag](vertrag.md). Folgende Mindestabdeckung ist öffentlicher Rolleninput; konkrete Fälle und Sollwerte nicht.

## 1. R1–R6 und Mindestabdeckung

| Kategorie | BF | SQL | UP |
| --- | --- | --- | --- |
| R1 | Formular `username`, `password`, `Login`, GET | Formular `id`, `Submit`, GET | Formular `uploaded`, `Upload`, POST-Multipart/CSRF |
| R2 | Mindestens zwei getrennte gültige Paare → 200/Erfolg und zugehöriger Avatar | Mindestens zwei getrennte vorhandene IDs → 200/zugehöriger Vor-/Nachname | Mindestens zwei getrennte gültige Bilder → 200/gespeicherte Datei und Erfolg |
| R3 | Sowohl falsches nicht leeres Passwort als auch unbekannte Kennung → 200/negativ, kein Erfolg/Avatar | Mindestens zwei unbekannte gültige IDs → 200/kein Datensatz | Mindestens ein wirksam kontrollierter Schreibfehler → 500/behandelter Fehler, keine neue/teilweise Datei, alte unverändert |
| R4 | Jedes fehlende/leere Pflichtfeld einzeln und beide gemeinsam → 422/Eingabefehler | Fehlende und leere ID → 422/Eingabefehler | Fehlender Dateipart und leerer File-Part → 422/Eingabefehler |
| R5 | Mindestens zwei verschiedene Benutzer korrekt zugeordnet; gesamte DB unverändert | Mindestens zwei verschiedene IDs korrekt zugeordnet; gesamte DB unverändert | Mindestens zwei verschiedene Bildinhalte bytegleich; Originalname/Pfad korrekt; Ausgangsbestand unverändert |
| R6 | Eine Folge positiv → negativ → anderer positiver Vorgang ohne Ergebnisreste | Eine Folge positiv → negativ → anderer positiver Vorgang ohne Ergebnisreste | Mindestens drei verschiedene gültige Namen; zuvor gespeicherte Dateien bleiben bytegleich |

R4 verlangt bei vorhandenem Aktionsfeld weder Erfolg noch Datensatz/Modulavatar/Pfad und keine Änderung von DB/Uploadinventar. Keine Aktion bedeutet Formularanzeige. Datenintegrität gilt zusätzlich **nach jeder Anfrage** aller Kategorien. R5/R6 sind eigene Fälle mit eigenen Zwecken, obwohl sie Teilfunktionen aus R2/R3 wiederverwenden. Zwischen Fällen wird DB, Uploadbestand, Session und Cache zurückgesetzt; innerhalb einer Sequenz bleiben sie erhalten. Der vertrauenswürdige Prüfer protokolliert Reset und R3-Rechtekontrolle. Die R3-Präparation wird nach dem Fall zurückgesetzt. Eine technische Messlücke kann die Kategorie nicht allein scheitern lassen.

Jeder externe funktionale Fall besitzt eine feste ID, genau eine R-Zuordnung und eine oder mehrere feste Assertions. Eine Sequenz ist ein Fall mit mehreren Schritten, keine zusätzliche unabhängige Beobachtung. Jede Kategorie hat Gewicht `1/6`; Fall-/Assertionsanzahl verändert das nicht. Alle einer Kategorie zugeordneten Fälle/Assertions müssen bestehen. Ein fachlich belegtes Scheitern genügt für `z=0`, auch neben weiteren Messlücken; ohne solches Scheitern ist `z=1` nur bei vollständigem Bestehen möglich, ansonsten bleibt `z=null` mit Ursache. Kategorien sind keine unabhängigen statistischen Beobachtungen. Die sechs R-Werte werden zusammen mit F und vollständigem Erfolg berichtet, keine nachträgliche Umgewichtung.

## 2. T1–T5 und Belegregeln

| Kriterium | Bestehensregel | Erforderlicher Beleg / Prüfinstanz |
| --- | --- | --- |
| T1 | Kandidat startet im eingefrorenen Gerüst unter bereitgestellten Voraussetzungen | Syntax-/Bootlog mit Exitstatus, Version/Hashes und Kontrolllauf des identischen intakten Gerüsts; technisch prüfen, Ursache dokumentieren |
| T2 | Jede Modulaktion über registrierte Web-Route an Methode eines modulspezifischen Controllers; kein bloßes Einbinden ausführbarer Legacy-Verarbeitung | Routenauflösung und tatsächlicher Aufrufpfad mit Datei-/Zeilen-/Hashbeleg; **Mensch** prüft |
| T3 | Controller verwendet tatsächlich Blade für Formular **und** Ergebnis; kein vollständiger direkter HTML-Ersatz | Controller-Viewaufruf und zugehörige Blade-Datei, unterstützende HTTP-Befunde; **Mensch** prüft |
| T4 | DB über bereitgestellte Laravel-Anbindung; UP über Laravel-Request-/Dateischnittstellen und vereinbarten Speicher; keine fest eingebauten Antworten | Codepfad/Zeilen sowie unabhängige Integrationsbefunde mit wechselnden synthetischen DB-Fixtures bzw. Dateien; **Mensch** prüft; Eloquent/Query Builder gleichwertig |
| T5 | Gemeinsame Konfiguration, Schema, Zugang, Abhängigkeiten unverändert; Modullogik nur in öffentlicher Allowlist, interne Tests getrennt | Diff und Dateimanifest gegen Gerüsthash; semantischer Zweifel bleibt bis **menschlichem** begründeten Urteil offen |

T2–T4 werden für **jeden vorhandenen Kandidaten** manuell geprüft, auch wenn T1, T5 oder ein anderes T bereits scheitert. Nicht ausführbare Integration verhindert nicht das Lesen vorhandener Codepfade; wo ein notwendiger Beleg fehlt, bleibt das Einzelkriterium begründet offen. Bei fehlendem Kandidaten wird Nichtprüfbarkeit erfasst, nicht drei erfundene Negativurteile. Reiner HTTP-Erfolg, ein Hashvergleich bei semantischem Zweifel oder PHPStan ersetzt kein solches Urteil. Interne Namen, Layout und weitere Vorstellungen von „idiomatischem Laravel“ sind keine Kriterien. Ein Mensch darf den versiegelten Kandidaten bei der Bewertung nicht reparieren; weder automatische noch manuelle Codereparatur gehört in den Evaluator.

Je Einzelurteil speichern: Rubrikversion, Kandidatenhash/Seal, Kriterium, `pass|fail|open|technical_missing|unclear`, Begründung, konkrete Datei+Zeilen+Dateihash oder Prüfloghash, prüfende Person/Instanz, UTC-Zeit und Revision. Für T4 zusätzlich Integrationseingaben/-logs; für T1 Kontrolllog. Ein menschlicher Entwurf darf `open` bleiben; Prüferperson und -datum werden nie synthetisch als echte Freigabe vorbelegt. Neue Entscheidungen erzeugen Revisionen, alte Urteile/Originale bleiben erhalten. Die spätere Benutzeroberfläche zeigt technischen Status, menschliche Prüfung und Freigabe separat.

## 3. Ursachen und aggregierte Sollurteile

| Zustand | Assertion/Fall | z-Kategorie | T / F |
| --- | --- | --- | --- |
| Vollständig erfüllte Funktion, gültige Messung | `pass` | 1, wenn alle zugeordneten Assertions bestehen | T=1 erst nach fünf belegten T-Erfüllungen; dann F=Summe(z)/6, sofern alle sechs bestimmbar |
| Belegter fachlicher Verhaltensfehler | betroffene Assertions `fail`, Soll/Ist und Ursache | 0 für jede dadurch betroffene Kategorie | R-Fehler allein setzt T nicht auf 0; bei T=1 regulärer R-Score |
| Belegter T-Verstoß, Kandidat startfähig | R weiterhin soweit ausführbar prüfen | messbare z erhalten | T=0 und F=0, auch wenn übrige Kriterien/Messungen offen sind |
| Kandidatenbootfehler oder fehlender Kandidat aus **belegter fachlicher Generierungsursache** | nicht ausführbare R-Fälle `blocked_by_candidate`, Beleg erforderlich | 0 für blockierte Kategorien | T=0/F=0; übrige T einzeln belegen oder offen halten |
| Evaluator-/Infrastruktur-/Providerfehler ohne belegten Kandidatenverstoß | `technical_missing`, Fehlerlog | null, sofern kein anderer fachlicher Fehler der Kategorie belegt | T/F fehlend, soweit technisch nicht bestimmbar; bereits belegtes T=0 hat Vorrang |
| Manueller Abbruch ohne bewertbaren Endstand | `technical_missing`, Ursache `manual_abort` | null ohne fachlichen Beleg | kein fingiertes T=0/F=0 |
| Unklarer API-/Transportausgang, Ursache ungeklärt | `unclear`, Unsicherheitsbeleg | null ohne fachlichen Beleg | T/F offen; keine zusätzliche Generierung als stiller Ersatz |
| Menschliches Pflichturteil ausstehend / T5 semantisch zweifelhaft | vorhandene R-Messungen erhalten | messbare z erhalten | ohne bereits belegten T-Verstoß T/F `null`, Status `open` |

`null` bedeutet fehlend/unbewertet, **niemals null Punkte**. `T=1` verlangt fünf `pass`; ein belegtes `fail` begründet `T=0`, sonst bleibt T offen/fehlend. Bei T=0 steht F=0 unabhängig von weiteren Lücken. Bei T=1 ist F nur berechenbar, wenn alle sechs z bestimmbar sind. Vollständiger Erfolg ist 1 genau bei F=1, 0 bei bestimmtem F<1, sonst fehlend. Roh-Testpassquote separat: bestandene Fälle / alle vorgesehenen Fälle, mit ausdrücklich ausgewiesenen fehlenden/blockierten Fällen und Nenner; sie ist kein Ersatz für F und keine Umgewichtung. Die Zahl technischer Assertions ist kein Auswertungsnenner.

Spätere Messberichte binden `asset_version` (Vertrag, Rubrik, Suite, Fixture, Referenz, Gerüst/Tools als Hash+Version) und `measurement_attempt` an denselben versiegelten Kandidaten. Sie enthalten Fall-/Assertion-ID, Request/Response, konkrete Soll-/Istwerte, Status/Ursache und Logs; Nachmessung erhält Originalversuch/Kandidatenhash. Diese Felder sind ein Übergabevertrag, noch keine Datenbankimplementierung. Eine fehlende Messumgebung bzw. unwirksame Upload-Rechtesperre darf niemals R3=0 oder F=0 allein erzeugen. Quelle: Operationalisierung §§2.4/6.1 und [Plan §§4.1, 7.2](../../planung/02-Technischer-Implementierungsplan.md).
