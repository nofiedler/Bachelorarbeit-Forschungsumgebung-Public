<!-- K1-MAPPING-UP-M2-v0.1 -->
# Zuordnung am Originalcode

Multipart/form und uploaded/Upload (vulnerabilities/upload/index.php:51–56) → low.php:3–6 → DVWA_WEB_PAGE_TO_ROOT (vulnerabilities/upload/index.php:3) plus hackable/uploads und Originalbasename (low.php:5–6) → move_uploaded_file (low.php:9) → $html (low.php:11–15), Modulbody (vulnerabilities/upload/index.php:63), Seitenausgabe (dvwa/includes/dvwaPage.inc.php:425–431). Sessionzugang wird vorher geprüft (vulnerabilities/upload/index.php:6; dvwa/includes/dvwaPage.inc.php:124–155); DB-Aufruf (vulnerabilities/upload/index.php:14) ist vorgeschalteter Seitenaufbau, keine Upload-Datenverarbeitung.

Eigene explizitere Aufbereitung, kein zusätzliches Sollwissen. Technische Agentenquellenprüfung erfolgt; menschliche Prüfung offen.
