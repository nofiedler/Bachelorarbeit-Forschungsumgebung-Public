# Manuelle Prüfung: Schritt für Schritt für BF, SQL und File Upload

Du beantwortest für **jeden versiegelten Kandidaten drei Fragen**: Werden die Aktionen durch einen Modulcontroller verarbeitet (T2)? Entstehen Formular und Ergebnis mit Blade (T3)? Werden die vorgesehenen Laravel-Anbindungen tatsächlich benutzt (T4)? Du dokumentierst, **was du im gesicherten Code und in den unabhängigen Befunden gesehen hast**. Eine kurze, nachvollziehbare Begründung mit konkreten Fundstellen genügt; ein Gutachten über die gesamte Anwendung ist nicht nötig.

Diese Anleitung erläutert die bestehende [Rubrik, Abschnitt 2](vertraege/m2-v0.1/rubrik.md#2-t1t5-und-belegregeln) und den [Modulvertrag](vertraege/m2-v0.1/vertrag.md). Sie führt keine neuen Bestehenskriterien ein. Maßgeblich bleiben die an den jeweiligen Lauf gebundenen Versionen. T1 und T5 werden automatisch geprüft; T5 braucht bei semantischem Zweifel eine menschliche Klärung. R1–R6 und PHPStan/Larastan werden separat ausgewertet.

**Wichtig für den gezeigten T2-Eintrag:** Eine Zeile wie `Route::get(..., [Controller::class, 'show'])` belegt zunächst die Zuordnung. Für dein Urteil musst du auch die aufgerufene Methode und deren weitere Aufrufe lesen. Erst daraus wird erkennbar, ob der Controller die Modulaktion verarbeitet oder nur ein altes PHP-Skript ausführt.

## Schnellzugriff

- [Vorbereitung und Dateien](#1-vorbereitung-und-dateien)
- [Was in welches Formularfeld gehört](#2-was-in-welches-formularfeld-gehört)
- [Brute Force: T2, T3 und T4](#3-brute-force-bf)
- [SQL Injection: T2, T3 und T4](#4-sql-injection-sql)
- [File Upload: T2, T3 und T4](#5-file-upload-up)
- [Integrationsbefunde lesen](#6-integrationsbefunde-lesen)
- [Unsicherheit, Fehler und Abschluss](#7-unsicherheit-fehler-und-abschluss)

## 1. Vorbereitung und Dateien

1. Öffne **den konkreten Lauf → Manuelle Prüfung**. Kontrolliere Modul und Laufzuordnung. Bewerte den versiegelten Endstand, nicht eine frühere Agentenantwort, einen Zwischenstand oder eine Referenzlösung.
2. Öffne unter **Passende Dateien öffnen** die verlinkten Dateien. Sie erscheinen mit Zeilennummern in einem eigenen Tab; das Formular bleibt geöffnet. Verwende für den Nachweis genau diese Zeilennummern.
3. Fehlt ein aufgerufener Helfer in den vorgeschlagenen Dateien, öffne **Weitere PHP-Dateien dieses gesicherten Laufstands**. Unter **Laravel-Code → Alle Projektdateien** findest du den vollständigen Bestand. Ein ausgelagerter Helfer kann die eigentliche Verarbeitung enthalten.
4. Für längere Codepfade kannst du unter **Laravel-Code** eine Prüfkopie herunterladen und den darin enthaltenen `laravel`-Ordner im Editor öffnen. Der Start gemäß beiliegender `START.md` ist zum Lesen des Codes nicht erforderlich. Verändere den Kandidaten nicht. Die Entwicklungsansicht der Prüfkopie ersetzt den gespeicherten unabhängigen Integrationsbefund nicht.
5. Lies T2, T3 und T4 einzeln. Auch wenn ein Kriterium schon scheitert, prüfst du die anderen, soweit ihre Belege vorliegen.

| Datei/Bereich im Kandidaten | Was du dort suchst |
| --- | --- |
| `routes/study.php` | Routen des aktuellen Moduls, HTTP-Methode, zugeordnete Controllerklasse und Methode. |
| `app/Http/Controllers/Study/…php` | Genau die durch die Route aufgerufene Methode: Eingabe → Verarbeitung bzw. Helfer → View/Antwort. |
| `app/Study/…php` | Vom Controller tatsächlich aufgerufene Helfer; auch Eloquent-Modelle dürfen hier liegen. Folge `use`-Import und Namespace. |
| `resources/views/study/…blade.php` | Die tatsächlich verwendete Formular-/Ergebnisdarstellung; gegebenenfalls weitere eingebundene Views, Layouts oder Komponenten. |
| `config/database.php` und bei UP `config/filesystems.php` | Gemeinsame Anbindung zur Einordnung des benutzten Anschlusses bzw. Speicherziels. Nicht bearbeiten. |
| **Ergebnisse → R5 → Testfälle und Originalbefunde** | Eingaben und unabhängige Daten-/Dateibeobachtungen als Unterstützung für T4. |

Die Namen von Controller, Methoden und Blade-Dateien sind **frei**. Ein `SqlInjectionController.php` oder eine Methode `show()` muss daher nicht existieren. Folge den wirklichen Verweisen. Die erlaubten Modulbereiche und die gemeinsame Anbindung stehen im [Vertrag, §§2 und 4](vertraege/m2-v0.1/vertrag.md#4-schreiballowlist-und-isolation); die Bedienpfade entsprechen der [Prüfoberfläche](../src/research_env/templates/review.html) und der [Codeansicht](../src/research_env/templates/run_code.html).

## 2. Was in welches Formularfeld gehört

| Formularfeld | Deine Eingabe |
| --- | --- |
| **Dein Urteil** | **Erfüllt** bei belegter Erfüllung des konkreten Kriteriums; **Nicht erfüllt** bei einem belegten Verstoß; **Noch nicht sicher** bei einer ungeklärten Frage oder fehlenden notwendigen Belegen. |
| **Was hast du festgestellt?** | Beschreibe den überprüften Weg und das Ergebnis. Nenne alle wichtigen Dateien mit Zeilen. Bei T4 zusätzlich Messrevision, konkrete Fall-/Befundreferenzen, beobachtete Eingaben/Ausgaben und deren Zusammenhang mit dem Code. |
| **Welche Datei belegt deinen Befund?** | Wähle die wichtigste vorhandene Belegdatei **dieses Laufs**. Beispielsweise die Controllerdatei, in der die Datenbankabfrage oder eine unzulässige Legacy-Einbindung steht. Weitere Dateien kommen in den Begründungstext. |
| **Welche Zeilen?** | Eine positive Zeilennummer oder ein zusammenhängender Bereich, beispielsweise `12` oder `12-18`. Keine Liste wie `12, 18, 31`, kein `L12` und keine erfundene Zeilennummer. Für getrennte Fundstellen eine Hauptstelle auswählen und die übrigen im Text nennen. |
| **Zugehöriger Integrationsbefund** (T4) | Die tatsächlich gelesene, gültige unabhängige Integrationsprüfung dieses Kandidaten auswählen. Die automatische Vorauswahl ist noch kein Nachweis, dass du ihren Inhalt geprüft hast. |
| **Dein Name** | Dein tatsächlicher Name als prüfende Person. Bei synthetischen Demos kennzeichnet die Anwendung den Eintrag zusätzlich als Demo-Bewertung. |
| **Vermutete Ursache** (optional) | Nur eine Erklärung, die du nicht sicher belegen kannst, etwa „Möglicherweise wurde der Rückgabewert des Uploads übersehen“. Die beobachtete Tatsache gehört in das Hauptfeld. |
| **Bewertungswerkzeug** | Die zum Lauf bzw. zu seinen Belegen passende Zuordnung beibehalten. Keine Versionsänderung vornehmen, um ein Speichern zu erzwingen. Bei einer Fehlermeldung erst die Zuordnung klären. |

Lauf-/Kandidatenbindung, Rubrik, Dateihash des ausgewählten Artefakts, Prüfername, Zeitpunkt und Revision werden über die Anwendung gespeichert. Du musst weder Hashes berechnen noch IDs abtippen. **Die fachliche Begründung und die passenden Zeilen musst du selbst prüfen.** Grundlage: [Rubrik §2](vertraege/m2-v0.1/rubrik.md#2-t1t5-und-belegregeln), [Formular und Hinweise](../src/research_env/templates/review.html), [Speicher- und Zuordnungsprüfung](../src/research_env/review_ui.py).

### Ein einfaches Schema für jede Begründung

```text
Geprüft: [Aktionen / Ausgabezweige / Datenfluss].
Beobachtung: [Was macht der Code tatsächlich?].
Belege: [Datei A, Zeilen …]; [Datei B, Zeilen …].
Bei T4: [Integrationsrevision, Fälle/Befund-IDs und beobachteter Zusammenhang].
Urteil: [Kriterium] ist [erfüllt / nicht erfüllt / offen], weil [konkreter Grund].
```

**Alle folgenden Textvorlagen sind auszufüllen.** Die Platzhalter sind keine festgestellten Befunde. Kopiere weder „erfüllt“ noch „kein Legacy-Aufruf“ in ein Urteil, ohne das tatsächlich geprüft zu haben. Es gibt keine feste Satz- oder Zeilenzahl für eine ausreichende Begründung.

## 3. Brute Force (BF)

Öffentlicher Modulpfad: `/study/brute`. GET ohne `Login` zeigt das Formular; GET mit vorhandenem `Login` verarbeitet `username` und `password`. Auch ein leeres Aktionsfeld zählt als vorhanden. Das BF-Modul prüft ein Benutzer-/Passwortpaar gegen den bereitgestellten Datenbestand; es ist **keine neue Anmeldung in der Forschungsanwendung**. Grundlage: [Vertrag §§1–3](vertraege/m2-v0.1/vertrag.md#1-gemeinsame-http--und-dom-schnittstelle).

### T2 – Controller und Routen

- Öffne `routes/study.php` und finde `/study/brute`. Notiere zugeordnete Klasse und Methode. Dieselbe GET-Route darf Anzeige und Verarbeitung behandeln; zwei Methoden sind keine Pflicht.
- Öffne die Methode. Verfolge sowohl den Weg ohne `Login` als auch den Weg mit `Login`. Prüfe weitere aufgerufene Helfer.
- Entscheidend ist, dass die Modulaktionen über einen modulspezifischen Controller laufen. Ein bloßes `include`/`require` der ausführbaren DVWA-Verarbeitung reicht nicht. Ein legitimer neuer Helfer ist dagegen nicht schon deshalb unzulässig, weil Logik ausgelagert wurde.

**Belegwahl:** Controller mit den betroffenen Methoden als Hauptbeleg; Route und eventuelle Helfer zusätzlich im Text nennen.

```text
Die GET-Route /study/brute verweist in routes/study.php, Z. […], auf
[Controller::Methode]. Die Methode in [Datei], Z. […], behandelt die
Formularanzeige und den Login-Verarbeitungspfad über [konkrete Verzweigung/
Helfer]. Bei der Prüfung dieses Aufrufpfads habe ich [Befund zur tatsächlichen
Verarbeitung und zu einer etwaigen Legacy-Einbindung] festgestellt.
Daraus folgt für T2: [Urteil und Grund].
```

### T3 – Blade für Formular und Ergebnis

- Suche die tatsächlich ausgeführten `view(…)`-/View-Response-Aufrufe im Controller. Öffne die referenzierten Blade-Dateien. Folge gegebenenfalls `@include`, Layouts oder Komponenten.
- Finde das Formular und die Darstellung der Verarbeitungsergebnisse: gültiges Paar mit Avatar, negative Antwort und Eingabefehler. Auch die reine Anzeige muss über die zugehörige View laufen.
- Prüfe, wo Ergebniswerte an die View übergeben und dort ausgegeben werden. Eine vorhandene `.blade.php` genügt nicht, wenn die Route sie gar nicht nutzt. Eine leere Blade-Hülle um vollständig aus der Legacy-Verarbeitung ausgegebenes HTML genügt ebenfalls nicht.

**Belegwahl:** Blade-Datei mit Formular/Ergebnis als Hauptbeleg; Controlleraufruf und weitere Ausgabezweige im Text. Ob etwa das Passwortfeld korrekt benannt ist oder der Avatar fachlich stimmt, gehört zu R1–R6; ein solcher Fehler allein beweist keinen T3-Verstoß.

```text
[Controllerdatei], Z. […], rendert [Viewname] und übergibt [Daten].
In [Blade-Datei], Z. […], entsteht das Formular; Z. […] stellen [Ergebnisse]
dar. Die weiteren Antwortzweige [Aufzählung] verwenden [Views/Fundstellen].
Für T3 ergibt sich [Urteil], weil [Formular und Ergebnis über Blade entstehen /
konkret benannter Ausgabeweg Blade umgeht / noch ungeklärter Pfad].
```

### T4 – Tatsächliche Laravel-Datenbankanbindung

- Folge dem Eingabewert bis zur Abfrage: Wo werden Benutzerkennung und Passwort verarbeitet, wo wird der passende Datensatz gelesen, woher kommt der Avatar?
- Suche den **tatsächlich aufgerufenen** Laravel-Datenzugriff, etwa Query Builder, Eloquent oder eine Laravel-eigene parametrisierte Abfrage. Ein `use DB` am Dateianfang allein beweist keine Nutzung. Bei ausgelagerten Abfragen den Helfer öffnen.
- Prüfe, ob eine eigene globale `mysqli`-/SQLite-Verbindung oder eine fest codierte Liste mit Benutzern/Avataren die bereitgestellte Anbindung ersetzt. Ein fester Statusmarker wie `SUCCESS` ist dagegen ausdrücklich vorgesehen und kein fest eingebauter fachlicher Ergebnisdatensatz.
- Lies die unabhängigen Integrationsbefunde mit verschiedenen Benutzerdaten. Lässt sich der ausgegebene Avatar auf den zugehörigen gespeicherten Datensatz und den gelesenen Codepfad zurückführen? Vorgehen in [Abschnitt 6](#6-integrationsbefunde-lesen).

Das vereinbarte MD5-Format dient hier der vorgegebenen Datenkompatibilität. Fordere keine Passwortmigration oder zusätzliche Anmeldung als neues T4-Kriterium. Quelle: [Vertrag §§2–3](vertraege/m2-v0.1/vertrag.md#2-datenbank-zugang-und-technische-gerüstvoraussetzungen).

```text
[Datei], Z. […], liest den Benutzer über [konkrete Laravel-Anbindung].
Der Avatar für die Ausgabe stammt aus [Datensatz/Feld; Fundstelle].
Integrationsrevision […], Fälle/Befunde […]: Bei [Eingabereferenz A/B]
zeigt der Befund [tatsächliche unterschiedliche Daten/Avatarzuordnungen].
Das [stützt/widerspricht/klärt nicht] den gelesenen Datenfluss, weil […].
T4: [Urteil und Begründung].
```

## 4. SQL Injection (SQL)

Öffentlicher Modulpfad: `/study/sqli`. GET ohne `Submit` zeigt das Formular; GET mit vorhandenem `Submit` verarbeitet `id`. Für diesen Versuch geht es um legitime IDs gemäß Vertrag, **nicht um einen zusätzlichen Angriffstest mit SQL-Injection-Payloads**. Quelle: [Vertrag §§1 und 3](vertraege/m2-v0.1/vertrag.md#3-moduldomänen-und-reaktionen).

### T2 – Controller und Routen

- Öffne `routes/study.php`, finde `/study/sqli` und folge der zugeordneten Methode.
- Verfolge Formularanzeige und Verarbeitung mit `Submit`, einschließlich aufgerufener Helfer.
- Prüfe die tatsächliche Modulverarbeitung im Controllerpfad. Eine Controllerdatei mit passendem Namen oder nur die Routenregistrierung reicht nicht; ein bloßer Aufruf des ausführbaren Legacy-Skripts erfüllt T2 nicht.

**Belegwahl:** Controller-Hauptstelle, dazu Route und Helfer im Text.

```text
/study/sqli ist in routes/study.php, Z. […], [Controller::Methode] zugeordnet.
[Controllerdatei], Z. […], behandelt Anzeige und Submit-Verarbeitung über […].
Die weitere Verarbeitung läuft über [Helfer/Fundstellen oder direkt dort].
Zum ausführbaren Legacy-Code habe ich im Aufrufpfad […] festgestellt.
T2: [Urteil und konkreter Grund].
```

### T3 – Blade für Formular und Abfrageergebnis

- Öffne die im Controller wirklich aufgerufene Blade-View und eventuell eingebundene Teile.
- Suche die Formularausgabe und die Ergebnisdarstellung für bekannte ID, unbekannte ID und fehlende/leere Eingabe.
- Verfolge, wie Vor-/Nachname aus Controllerdaten in die View gelangen. Prüfe alle relevanten Rückgaben; beispielsweise darf nicht nur das Formular Blade nutzen, während der Ergebniszweig vollständiges HTML direkt zurückgibt.

**Belegwahl:** View mit relevanter Ausgabe, ergänzt um den Controlleraufruf. Die exakte Namenszuordnung oder richtige Ergebniszahl prüfen die R-Kategorien; sie werden nicht allein wegen eines falschen Namens zu einem T3-Verstoß.

```text
Die Methode in [Controllerdatei], Z. […], rendert [Viewname].
[Blade-Datei], Z. […], enthält das Formular; Z. […] zeigen das Ergebnis
über [Variablen/Bedingungen]. Für negative und unvollständige Eingaben
sind [Fundstellen] maßgeblich. T3: [Urteil], weil […].
```

### T4 – Daten aus der bereitgestellten Datenbank

- Folge `id` bis zur tatsächlich ausgeführten Abfrage auf dem bereitgestellten Datenbestand. Prüfe, welche Verbindung verwendet wird und aus welchem Ergebnis Vor-/Nachname entstehen.
- Query Builder, Eloquent und Laravel-eigene parametrisierte Abfragen sind zulässig. Es ist kein bestimmter Methodenname oder ORM-Stil vorgeschrieben.
- Suche im tatsächlich genutzten Pfad nach Ersatzquellen: eigene globale `mysqli`-/SQLite-Verbindung, feste Namenslisten, `switch` über bekannte IDs oder scheinbare Abfragen, deren Ergebnis anschließend ignoriert wird.
- Gleiche die unabhängigen R5-Integrationsbefunde für unterschiedliche Datenzuordnungen mit diesem Codepfad ab. Lies Eingabe und Ist-Ausgabe; ein grünes Gesamtergebnis oder eine einzelne richtige Antwort allein ersetzt das nicht.

**Belegwahl:** Die Datei mit dem realen Datenzugriff; wenn dies ein Helfer ist, diesen auswählen und den Controlleraufruf zusätzlich nennen.

```text
[Datei], Z. […], verarbeitet die ID aus […] mit [konkreter Laravel-Abfrage].
Die ausgegebenen Felder stammen aus [Abfrageergebnis/Fundstelle].
Integrationsrevision […], Fälle/Befunde […]: [Eingabereferenzen] führen
zu [beobachteten Vor-/Nachnamen bzw. Abweichungen]. Der Zusammenhang
mit dem Code ist […]. T4: [Urteil und Grund].
```

## 5. File Upload (UP)

Öffentlicher Modulpfad: `/study/upload`. GET ohne `Upload` zeigt das Formular; POST-Multipart mit vorhandenem `Upload` verarbeitet `uploaded`. Vereinbarter Speicher: Disk `study_uploads`, Wurzel `storage/app/study/uploads`; der zurückgemeldete relative Pfad lautet `study/uploads/<Originalbasisname>`. Quelle: [Vertrag §§1 und 3](vertraege/m2-v0.1/vertrag.md#3-moduldomänen-und-reaktionen).

### T2 – Anzeige und Upload über Controller

- Prüfe in `routes/study.php` **GET und POST** für `/study/upload`.
- Folge beiden Zuordnungen. Gleiche oder unterschiedliche Controller-Methoden sind möglich; entscheidend ist ihr tatsächlicher Aufrufpfad.
- Lies den Weg von der POST-Eingabe über die Uploadverarbeitung bis zur Antwort und eventuelle Helfer. Prüfe, ob ausführbare Legacy-Verarbeitung nur eingebunden wird.

**Belegwahl:** Controller mit der Uploadverarbeitung; GET-/POST-Routen und Anzeige-Methode zusätzlich im Text.

```text
GET und POST auf /study/upload sind in routes/study.php, Z. […], den
Methoden […] zugeordnet. [Datei], Z. […], zeigt das Formular;
[Datei], Z. […], verarbeitet den Upload über […]. Der Aufrufpfad
[belegt die Controller-Verarbeitung / enthält folgende Legacy-Einbindung / ist unklar].
T2: [Urteil und Grund].
```

### T3 – Blade für Uploadformular und Rückmeldung

- Finde die View-Aufrufe für die GET-Anzeige und sämtliche POST-Antworten.
- Öffne die Views. Suche Formular, Erfolg mit Dateipfad, fehlende Datei und behandelte Speicherfehlermeldung.
- Prüfe, ob diese Ausgaben tatsächlich durch Blade entstehen. Formular allein genügt nicht; auch die Ergebnisantwort muss darüber laufen.

`multipart/form-data`, File-Control, Aktionsfeld und gültiges verborgenes `_token` gehören zur funktionalen Formularprüfung. Sie helfen dir beim Auffinden des Formulars, sind aber keine zusätzlichen T3-Stilkriterien. Für die gebundene CSRF1-Fassung gilt die [bestehende Präzisierung](vertraege/m2-v0.1-csrf1/addendum.md); schalte zum Ausprobieren keine Middleware ab.

```text
Die GET-/POST-Antworten in [Controllerdatei], Z. […], verwenden [Views].
[Blade-Datei], Z. […], enthält das Uploadformular; Z. […] stellen
[Erfolg/Pfad und Fehlerzweige] dar. Weitere Viewteile: […].
T3: [Urteil und Grund].
```

### T4 – Wirkliche Datei über Laravel im vereinbarten Speicher

- Folge dem Upload-Eingang. Suche die Laravel-Request-/UploadedFile-Verarbeitung, beispielsweise `$request->file('uploaded')`, und prüfe deren tatsächliche Verwendung. Die Schreibweise ist ein Suchbeispiel, keine verpflichtende Musterlösung.
- Folge dem Dateiobjekt bis zur Speicherung. Laravel-Datei-/Speicherschnittstellen können unterschiedlich verwendet werden. Entscheidend sind die tatsächliche Verarbeitung und der vereinbarte Speicherbereich; ein bestimmter Methodenname wie `storeAs` ist keine zusätzliche Pflicht.
- Prüfe Disk bzw. Zielpfad und, falls nötig, `config/filesystems.php`. Eine aus dem Dateinamen zusammengesetzte Erfolgsmeldung beweist noch nicht, dass eine Datei gespeichert wurde.
- Gleiche die unabhängigen Eingabedateien und den gespeicherten Nachzustand ab: Werden tatsächlich die übertragenen unterschiedlichen Dateien geschrieben? Woher stammen der rückgemeldete Pfad und die gespeicherten Bytes? Die R5-Befunde zu Namen, Größe und SHA-256 helfen dabei.
- Beurteile einen Fehler kriteriumsbezogen: Eine Speicherung im falschen Bereich kann T4 verletzen. Ein ansonsten über die korrekte Anbindung gespeicherter Upload mit einer falschen Textdarstellung kann dagegen zunächst ein R-Fehler sein. Dokumentiere den nachgewiesenen Zusammenhang.

**Belegwahl:** Datei mit Request-/Dateiverarbeitung und Speicherung; den Zielpfad sowie weitere Hilfs-/Konfigurationsdateien im Text ergänzen.

```text
[Datei], Z. […], nimmt uploaded über [Laravel-Schnittstelle] entgegen.
Gespeichert wird über […] nach [tatsächlich ermittelter Disk/Zielpfad].
Belege dafür: […]. Integrationsrevision […], Fälle/Befunde […]:
Die Dateien [Eingabereferenzen] ergeben [beobachtete gespeicherte Namen,
Größen/Hashes bzw. Abweichungen]. Der ausgegebene Pfad stammt aus […].
T4: [Urteil und konkreter Grund].
```

Die Modulchecklisten erläutern jeweils ausschließlich [T2–T4 der Rubrik](vertraege/m2-v0.1/rubrik.md#2-t1t5-und-belegregeln). Prüfe keine zusätzlichen Dateitypen, ausführbaren Uploads, Namenskollisionen oder Sicherheitsverbesserungen als neue Bestehensbedingungen.

## 6. Integrationsbefunde lesen

**T4 besteht aus Codeprüfung und unabhängigen Integrationsbelegen.** Die Anwendung erzeugt dafür `T4_integration` aus den R5-Beobachtungen; die zugehörigen Rohbelege umfassen auch den Ablauf der unabhängigen Messung. Deshalb ist nicht jeder angebotene Rohbeleg bereits der gesuchte Datenvergleich. Technische Grundlage: [Erzeugung der Messungen](../src/research_env/evaluation.py), [Darstellung der Fälle](../src/research_env/templates/results.html).

1. Öffne im T4-Schritt **Integrationsbefund ansehen**. Notiere die Revision, die du tatsächlich untersuchst. Einträge „Befund öffnen 1/2/…“ sind nummerierte Artefaktlinks, keine Fachurteile.
2. Zur gezielten Orientierung öffne zusätzlich **Ergebnisse → Funktionale Korrektheit → R5 → Testfälle und Originalbefunde**. Dort stehen Fall-/Assertion-IDs mit **Eingabe** und **Originalbefund**. Ordne sie der gleichen Messausführung zu; bei mehreren Messrevisionen nicht ungeprüft alte und neue Befunde vermischen.
3. Lies den Inhalt des Eingabebelegs: Fallkennung und Schritte. Lies im zugehörigen Originalbefund die tatsächlich gesendete Anfrage (`sent`), Antwort (`response`) und den beobachteten Nachzustand (`snapshot`), soweit vorhanden. `case_id` und Schritt helfen bei der Zuordnung. Bei blockierten Fällen kann stattdessen nur ein Eingabe-/Fehlerbeleg vorliegen.
4. In Assertion-Belegen stehen `expected`, `actual`, `status` und `cause`. **`passed`** bedeutet, dass diese konkrete Assertion bestanden wurde; **`failed`** eine beobachtete Abweichung. **`technical_missing`** oder ein blockierter Fall sind keine positiven Integrationsbelege. Eine Messung kann abgeschlossen sein und trotzdem fehlende Einzelbeobachtungen enthalten.
5. Prüfe die für den Datenfluss relevanten, unterschiedlichen Eingaben und deren Befunde. BF: passende Benutzer-/Avatarzuordnung. SQL: passende ID-/Namenszuordnung. UP: tatsächliche Speicherung der unterschiedlichen Dateien im Ziel, einschließlich Namen/Bytes. Lies auch Abweichungen; wähle nicht nur einen passenden Erfolgsfall aus.
6. Verbinde das mit dem Code: **Welche gelesene Stelle erklärt die beobachteten Daten?** Stimmen Code und Beobachtung nicht zusammen, kläre die Ursache oder speichere begründet offen. Ein fehlgeschlagener R5-Test ist nicht automatisch ein T4-Verstoß; beispielsweise kann eine falsche fachliche Auswahl trotzdem über die vorgeschriebene Datenbankanbindung erfolgen.
7. Wähle im Formular genau die untersuchte gültige Integrationsrevision. Notiere im Text Fall-/Befundkennungen und die wesentliche Beobachtung. Du musst keine vollständigen JSON-Dateien ins Textfeld kopieren. Unter **Herkunft und Dateiintegrität** eines Belegs findest du dessen Beleg-ID und Hash.

Die aktuelle Ergebnisseite zeigt die für sie ausgewählten Messwerte. Falls du die Zuordnung zu einer anderen Integrationsrevision nicht sicher herstellen kannst, nutze deren eigene Rohbelege und die **Nachweise** des Laufs oder halte diese Unsicherheit ausdrücklich fest. Fehlende Integration wird nicht durch das bloße Starten der lokalen Prüfkopie ersetzt. Externe Befunde bleiben außerhalb der Agenteninputs.

## 7. Unsicherheit, Fehler und Abschluss

### Wann welches Urteil?

| Beobachtung | Vorgehen |
| --- | --- |
| Du hast alle relevanten Pfade gelesen und die notwendigen Belege stützen das Kriterium. | **Erfüllt**, begründen und abschließen. |
| Ein konkreter Pfad verletzt das Kriterium, z. B. reine Legacy-Einbindung, vollständige direkte HTML-Ausgabe statt Blade oder eine eigene globale Datenbankverbindung. | **Nicht erfüllt**, genaue Stelle und verletztes Kriterium nennen. Die anderen Kriterien trotzdem prüfen. |
| Du verstehst einen Helfer noch nicht oder ein notwendiger Befund fehlt. | **Noch nicht sicher → Entwurf speichern**; konkret nennen, was noch ungeklärt ist. |
| PHPStan meldet Fehler oder ein Funktionstest scheitert. | Daraus allein kein T2-/T3-/T4-Urteil ableiten. Den Code und den jeweiligen Zusammenhang prüfen. |
| Es gibt keinen versiegelten Kandidaten. | Keine drei negativen Codeurteile erfinden. Den dokumentierten Fehlversuch bzw. fehlenden Kandidaten über den vorgesehenen Ergebnisweg behandeln. |
| Das Formular akzeptiert einen Abschluss nicht. | Fehlenden Beleg/Zeilen/Zuordnung klären. Keine beliebigen Dateien, Zahlen oder Messungen eintragen, damit der Button funktioniert. |

**Beispiel eines negativen T2-Befunds, nur falls so beobachtet:**

```text
Die Route verweist auf [Controller::Methode]. Diese führt in [Datei],
Z. […], ausschließlich das ausführbare Legacy-Skript […] aus.
Die eigentliche Modulverarbeitung bleibt dort. T2 ist deshalb nicht erfüllt.
Belege: [Route + Zeilen], [Controller + Zeilen], [Legacy-Datei + Zeilen].
```

**Beispiel eines offenen T4-Befunds:**

```text
Im Code ist die Laravel-Anbindung über […] in [Datei], Z. […], erkennbar.
Der erforderliche unabhängige Integrationsbefund fehlt / enthält für […]
keine auswertbare Beobachtung. Ich kann die tatsächliche Integration daher
noch nicht abschließend beurteilen. T4 bleibt offen; zu klären ist […].
```

**Fehlende Datei:** Unterscheide „nicht in der Vorschlagsliste“ von „im versiegelten Bestand tatsächlich nicht vorhanden“. Prüfe zuerst **Alle Projektdateien** und den Aufrufpfad. Ist z. B. eine verwendete View nachweislich nicht vorhanden, beschreibe Pfad, suchbaren Bestand und aufrufende Codezeile. Wähle eine vorhandene Belegstelle, die den Befund zeigt; erfinde keine Zeile in einer fehlenden Datei. Fehlt ein belastbarer Beleg, speichere einen Entwurf.

**T4 ohne Integrationsbeleg:** Einen im Code erkannten Verstoß kannst du bereits als Beobachtung dokumentieren. Die aktuelle Oberfläche verlangt für ein abgeschlossenes T4-Urteil dennoch den zugehörigen Integrationsbeleg. Bis dieser vorliegt, als Entwurf festhalten. Nicht irgendeine andere Messung zuordnen. Diese Bediengrenze ist kein Grund, einen Codeverstoß zu verschweigen oder einen positiven Befund zu erfinden.

### Nach dem Speichern

1. Nutze **Bewertung speichern und weiter**, wenn das Urteil belegt abgeschlossen werden kann; sonst **Entwurf speichern**. Das Speichern erzeugt eine Revision, auch bei einer späteren Korrektur.
2. Ein Haken bzw. **Bewertet** bedeutet „gültiges abgeschlossenes Urteil vorhanden“, nicht automatisch „Erfüllt“. Kontrolliere deshalb auch das tatsächliche Urteil. Ein Entwurf ersetzt keine frühere gültige abgeschlossene Bewertung.
3. Nach T2–T4 unter **Ergebnisse** die automatischen Messungen, alle Einzelurteile und offenen Gründe prüfen. **Lauf abschließen** bestätigt den geprüften Ergebnisstand; es bedeutet nicht, dass der Kandidat alle Anforderungen erfüllt hat.
4. Bei Hauptläufen danach den aktuellen Bewertungsstand sichern und die tatsächliche Ablage bestätigen, bevor du den nächsten Lauf startest. Eine spätere Bewertungsänderung benötigt eine aktualisierte Sicherung.

Grundlage: [Rubrik §§2–3](vertraege/m2-v0.1/rubrik.md), [Revisions- und Formularregeln](../src/research_env/review_ui.py), [Ergebnisabschluss](../src/research_env/templates/results.html) und [Bedienablauf in der README](../README.md#eine-versuchsreihe-durchführen).

**Nicht zusätzlich bewerten:** Geschmack bei Dateinamen, Einrückung, CSS, Anzahl der Methoden, bevorzugter ORM-Stil oder nachträglich gewünschte Sicherheitsfunktionen. Prüfe bei K0 und K1 mit demselben gebundenen Maßstab. Ändere während der Bewertung weder Kandidat noch Rollenprompts oder Kontextpakete.
