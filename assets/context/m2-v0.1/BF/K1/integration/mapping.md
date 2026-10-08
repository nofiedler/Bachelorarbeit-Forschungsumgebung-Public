<!-- K1-MAPPING-BF-M2-v0.1 -->
# Zuordnung am Originalcode

Formularfelder/GET (vulnerabilities/brute/index.php:42–48) → low.php:3–9/12–28 (MD5 und DB-Prüfung) → Avatar aus DB (low.php:15–22) → $html in Modulbody (vulnerabilities/brute/index.php:55), weiter Seitenausgabe (dvwa/includes/dvwaPage.inc.php:425–431). Vorgeschalteter Zugang (vulnerabilities/brute/index.php:6; dvwa/includes/dvwaPage.inc.php:124–155) ist von der Modulprüfung getrennt; MySQL-Verbindung (vulnerabilities/brute/index.php:13; dvwa/includes/dvwaPage.inc.php:562–575).

Eigene explizitere Aufbereitung, kein zusätzliches Sollwissen. Technische Agentenquellenprüfung erfolgt; menschliche Prüfung offen.
