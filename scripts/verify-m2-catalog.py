#!/usr/bin/env python3
"""Prüft nur den deklarativen M2-Katalog; führt keinen Kandidaten aus."""

import argparse
import copy
import datetime
import hashlib
import json
import platform
import re
import struct
import sys
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CAT = ROOT / "evaluation/study_holdout/m2-v0.1"
PUB = ROOT / "docs/vertraege/m2-v0.1"
MODULES = {"BF": "/study/brute", "SQL": "/study/sqli", "UP": "/study/upload"}
R_KEYS = [f"R{i}" for i in range(1, 7)]
T_KEYS = [f"T{i}" for i in range(1, 6)]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(name):
    return json.loads((CAT / name).read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(fixtures, cases_doc, references, outcomes):
    cases = cases_doc["cases"]
    all_ids = []
    assertion_ids = set()
    expected_columns = {"user_id", "first_name", "last_name", "user", "password", "avatar",
                        "last_login", "failed_login", "role", "account_enabled"}
    for dataset, rows in fixtures["datasets"].items():
        require(len({r["user_id"] for r in rows}) == len(rows), "Eindeutige IDs")
        require(len({r["user"] for r in rows}) == len(rows), "Eindeutige Benutzer")
        for row in rows:
            require(set(row) == expected_columns, "Vollständige Fixture-Spalten")
            require(1 <= row["user_id"] <= 999999, "ID-Domäne")
            require(re.fullmatch(r"[a-z0-9]{1,15}", row["user"]), "Benutzerdomäne")
            pw = fixtures["credentials"][dataset][row["user"]]
            require(re.fullmatch(r"[A-Za-z0-9]{1,32}", pw), "Passwortdomäne")
            require(hashlib.md5(pw.encode("ascii")).hexdigest() == row["password"], "MD5-Fixture")
            require(len(row["first_name"]) <= 15 and len(row["last_name"]) <= 15, "Namenslängen")
            require(len(row["avatar"]) <= 70, "Avatarlänge")
    for case in cases:
        all_ids.append(case["id"])
        mod = case["module"]
        require(mod in MODULES and case["category"] in R_KEYS, "Genau eine gültige R-Zuordnung")
        require(case["version"] == "M2-v0.1" and case["purpose"] and case["provenance"], "Fallprovenienz")
        require(case["setup"]["reset_before"] is True, "Reset zwischen Fällen")
        require(case["setup"]["reset_between_steps"] is False, "Zustand innerhalb Fall")
        require(case["setup"]["database"] in fixtures["datasets"], "Fixturebezug")
        require(bool(case["steps"]), "Fall ohne Schritte")
        for step in case["steps"]:
            req = step["request"]
            require(req["path"] == MODULES[mod], "Öffentlicher Pfad")
            require(req["method"] == ("POST" if mod == "UP" and case["category"] != "R1" else "GET"), "HTTP-Methode")
            targets = {a["target"] for a in step["assertions"]}
            require({"response.status", "dom.result", "database.full_schema_and_rows", "uploads.inventory"} <= targets, "Fehlende Pflichtassertion")
            for a in step["assertions"]:
                require(a["id"] not in assertion_ids, "Doppelte Assertion-ID")
                assertion_ids.add(a["id"])
                require(a["expected_verdict"] == "pass" and "expected" in a, "Fehlendes Sollurteil")
                if a["target"] == "dom.form":
                    if mod == "UP":
                        require(a["expected"].get("valid_csrf_token") is True, "Upload-CSRF bleibt verpflichtend")
                    else:
                        require("valid_csrf_token" not in a["expected"], "GET-CSRF ist unbewertet")
            if case["category"] == "R4":
                require(next(a["expected"] for a in step["assertions"] if a["target"] == "response.status") == 422, "R4-Status")
            f = req.get("file")
            if f and f["mode"] == "present":
                require(f["fixture"] in fixtures["files"], "Dateireferenz")
                name = f["filename"]
                require(len(name) <= 64 and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.(png|jpg|jpeg)", name), "Uploaddomäne Name")
                require(1 <= fixtures["files"][f["fixture"]]["size"] <= 100000, "Uploaddomäne Bytes")
    require(len(set(all_ids)) == len(all_ids), "Doppelte Fall-ID")
    for mod in MODULES:
        mc = [c for c in cases if c["module"] == mod]
        require({c["category"] for c in mc} == set(R_KEYS), "R-Vollständigkeit")
        require(len([c for c in mc if c["category"] == "R2"]) >= 2, "Zwei getrennte positive Fälle")
        r5 = [c for c in mc if c["category"] == "R5"]
        require(len(r5) >= 2, "R5-Mindestfälle")
        tags = {tag for c in mc for tag in c["tags"]}
        expected_tags = {"BF": {"wrong_password", "unknown_user", "username_missing", "username_empty", "password_missing", "password_empty", "both_missing", "both_empty"},
                         "SQL": {"unknown_id", "id_missing", "id_empty"},
                         "UP": {"write_failure", "file_missing", "file_empty"}}[mod]
        require(expected_tags <= tags, "R3/R4-Mindestabdeckung")
        if mod == "SQL":
            unknown = [c["steps"][0]["request"]["fields"]["id"] for c in mc if c["category"] == "R3"]
            require(len(set(unknown)) >= 2, "Zwei unbekannte SQL-IDs")
        key = "username" if mod == "BF" else "id"
        distinct = [c["steps"][0]["request"]["file"]["fixture"] if mod == "UP" else c["steps"][0]["request"]["fields"][key] for c in r5]
        require(len(set(distinct)) >= 2, "R5 unterschiedliche Benutzer/IDs/Dateien")
        if mod == "UP":
            require(len({fixtures["files"][f]["sha256"] for f in distinct}) >= 2, "R5 unterschiedliche Bildbytes")
        for seq in (c for c in mc if c["category"] == "R6"):
            require(len(seq["steps"]) >= 3, "R6 mindestens drei Schritte")
            if mod == "UP":
                require(len({s["request"]["file"]["filename"] for s in seq["steps"]}) >= 3, "R6 verschiedene Namen")
            else:
                markers = [next(a["expected"]["marker"] for a in s["assertions"] if a["target"] == "dom.result") for s in seq["steps"]]
                require(markers == ["SUCCESS", "NEGATIVE", "SUCCESS"], "R6 Folge")
                require(seq["steps"][0]["request"]["fields"][key] != seq["steps"][-1]["request"]["fields"][key], "R6 anderer positiver Datensatz")
    refs = references["references"]
    require(len({r["id"] for r in refs}) == len(refs), "Doppelte Referenz-ID")
    for ref in refs:
        e = ref["expected"]
        mc = [c for c in cases if c["module"] == ref["module"]]
        require(set(e["case_scope"]) == {c["id"] for c in mc}, "Vollständiger Referenz-Fallumfang")
        ai = {a["id"] for c in mc for s in c["steps"] for a in s["assertions"]}
        fails = set(e["assertions"]["fail_ids"])
        require(fails <= ai, "Unbekannte Fehlerassertion")
        require(len(fails) == len(e["assertions"]["fail_ids"]), "Doppelte Fehlerassertion")
        require(set(e["R"]) == set(R_KEYS) and set(e["T_criteria"]) == set(T_KEYS), "Vollständige Sollvektoren")
        for rk in R_KEYS:
            computed = 0 if e["assertions"]["default"] == "blocked_by_candidate" or any(a["id"] in fails for c in mc if c["category"] == rk for s in c["steps"] for a in s["assertions"]) else 1
            require(e["R"][rk] == computed, "R-Sollvektor widerspricht Assertion-Soll")
        require(e["T"] == (0 if 0 in e["T_criteria"].values() else 1), "T-Sollaggregation")
        require(e["F"] == {"numerator": 0 if e["T"] == 0 else sum(e["R"].values()), "denominator": 6}, "F-Sollaggregation")
        require(ref["human_review"] == "open" and ref["execution"] == "not_implemented", "Keine fingierte Referenzprüfung")
    for mod in MODULES:
        require({f"GOOD-A-{mod}", f"GOOD-B-{mod}"} <= {r["id"] for r in refs}, "Zwei Referenzstrukturen")
        for criterion in R_KEYS + T_KEYS:
            require(any(r["module"] == mod and criterion in r.get("targets", []) for r in refs), "Fehlender Gegenentwurf")
        require(any(r["id"] == f"BAD-{mod}-T2-FACADE" for r in refs), "Fehlende Legacy-Fassade")
    scenarios = outcomes["scenarios"]
    require(len({s["id"] for s in scenarios}) == len(scenarios), "Doppelte Ursachen-ID")
    for s in scenarios:
        require(set(s["R"]) == set(R_KEYS) and set(s["T_criteria"]) == set(T_KEYS), "Vollständige Ursachenvektoren")
        if 0 in s["T_criteria"].values():
            require(s["T"] == 0 and s["F"] == {"numerator": 0, "denominator": 6}, "Belegtes T0 dominiert Lücken")
        elif None in s["T_criteria"].values():
            require(s["T"] is None and s["F"] is None, "Offene T-Werte")
        elif None in s["R"].values():
            require(s["T"] == 1 and s["F"] is None, "Technische R-Lücke")
        else:
            require(s["F"] == {"numerator": sum(s["R"].values()), "denominator": 6}, "Ursachen-F-Soll")
    return {"functional_cases": len(cases), "assertions": len(assertion_ids), "reference_designs": len(refs), "classification_scenarios": len(scenarios)}


def validate_files(fixtures):
    for f in fixtures["files"].values():
        path = CAT / f["path"]
        require(path.stat().st_size == f["size"] and digest(path) == f["sha256"], "Dateihash/Größe")
        if path.suffix == ".png":
            data = path.read_bytes()
            require(data[:8] == b"\x89PNG\r\n\x1a\n", "PNG-Signatur")
            offset, compressed, dims = 8, b"", None
            while offset < len(data):
                n = struct.unpack(">I", data[offset:offset+4])[0]
                kind, body = data[offset+4:offset+8], data[offset+8:offset+8+n]
                crc = struct.unpack(">I", data[offset+8+n:offset+12+n])[0]
                require(zlib.crc32(kind + body) & 0xffffffff == crc, "PNG-CRC")
                if kind == b"IHDR":
                    dims = struct.unpack(">IIBBBBB", body)
                if kind == b"IDAT":
                    compressed += body
                offset += n + 12
            require(dims and dims[2:] == (8, 2, 0, 0, 0), "PNG-RGB-Domäne")
            require(len(zlib.decompress(compressed)) == dims[1] * (1 + 3 * dims[0]), "PNG-Pixelinhalt")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    inputs = [read(n) for n in ("fixtures.json", "cases.json", "references.json", "outcomes.json")]
    counts = validate(*inputs)
    validate_files(inputs[0])
    manifest = read("manifest.json")
    public_paths = sorted(str(p.relative_to(ROOT)) for p in PUB.iterdir() if p.is_file())
    require(manifest["public_role_allowlist"] == public_paths, "Öffentliche Allowlist")
    for path, sha in manifest["sha256"].items():
        require(digest(ROOT / path) == sha, "Manifesthash: " + path)
    public_text = "\n".join(p.read_text() for p in PUB.iterdir() if p.is_file())
    secret_values = list(inputs[0]["files"])
    for dataset, rows in inputs[0]["datasets"].items():
        secret_values += [r["user"] for r in rows] + [r["avatar"] for r in rows]
        secret_values += list(inputs[0]["credentials"][dataset].values())
    require(not any(v in public_text for v in secret_values), "Holdoutwert in öffentlicher Datei")
    for path in list(PUB.glob("*.md")) + list(CAT.glob("*.md")):
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
            if "://" in target or target.startswith("#"):
                continue
            target = target.strip("<>").split("#")[0]
            require((path.parent / target).exists(), "Lokales Linkziel: " + target)
    negative_checks = []
    for name in ("duplicate_case", "missing_R4", "wrong_F", "unknown_assertion",
                 "GET_BF_csrf_constraint", "GET_SQL_csrf_constraint", "missing_upload_csrf"):
        altered = copy.deepcopy(inputs)
        if name == "duplicate_case":
            altered[1]["cases"].append(copy.deepcopy(altered[1]["cases"][0]))
        elif name == "missing_R4":
            altered[1]["cases"] = [c for c in altered[1]["cases"] if "both_empty" not in c["tags"]]
        elif name == "wrong_F":
            altered[2]["references"][0]["expected"]["F"]["numerator"] = 0
        elif name == "unknown_assertion":
            altered[2]["references"][0]["expected"]["assertions"]["fail_ids"] = ["ABSENT"]
        else:
            mod = {"GET_BF_csrf_constraint": "BF", "GET_SQL_csrf_constraint": "SQL", "missing_upload_csrf": "UP"}[name]
            form_case = next(c for c in altered[1]["cases"] if c["module"] == mod and c["category"] == "R1")
            form = next(a["expected"] for a in form_case["steps"][0]["assertions"] if a["target"] == "dom.form")
            if mod == "UP":
                del form["valid_csrf_token"]
            else:
                form["valid_csrf_token"] = False
        try:
            validate(*altered)
        except ValueError as exc:
            negative_checks.append({"mutation": name, "status": "passed", "rejection": str(exc)})
        else:
            raise ValueError("Fehlerhafte Katalogmutation nicht erkannt: " + name)
    report = {"status": "passed", "scope": "declarative_catalog_only", "platform": platform.platform(),
              "executed_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "python": sys.version, "input_manifest_sha256": digest(CAT / "manifest.json"),
              "checker_sha256": digest(Path(__file__)), "counts": counts, "negative_checks": negative_checks,
              "not_executed": ["Laravel reference runtime", "candidate evaluator", "human T2–T4 review", "human approval", "G03", "G06"]}
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        with args.output.open("x") as destination:
            destination.write(output)
    print(output, end="")


if __name__ == "__main__":
    main()
