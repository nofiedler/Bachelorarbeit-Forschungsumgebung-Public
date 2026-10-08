<!-- K1-MAPPING-SQL-M2-v0.1 -->
# Zuordnung am Originalcode

Formular id/Submit (vulnerabilities/sqli/index.php:46–59) → low.php:3–8 → MySQL-Abfrage low.php:10–26 → first_name/last_name in $html (low.php:15–22) → Modulbody (vulnerabilities/sqli/index.php:68), Seitenausgabe (dvwa/includes/dvwaPage.inc.php:425–431). Verbindung wird vorher hergestellt (vulnerabilities/sqli/index.php:14; dvwa/includes/dvwaPage.inc.php:562–575). Das MySQL-Ziel ist bereits K0 bekannt.

Eigene explizitere Aufbereitung, kein zusätzliches Sollwissen. Technische Agentenquellenprüfung erfolgt; menschliche Prüfung offen.
