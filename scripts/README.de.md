# Skripte

[English](README.md) | [Deutsch](README.de.md)

Referenz der Betriebs-Skripte von `vb-api`. Das große Bild (was wo läuft, was bei einem Ausfall zu tun ist, wie man das System neu aufbaut) steht im [`vb-deploy`-README](https://github.com/K-O-St-V-Vindobona-II/vb-deploy/blob/main/README.de.md), das dieses Dokument als bekannt voraussetzt; Kapitel [4.2](https://github.com/K-O-St-V-Vindobona-II/vb-deploy/blob/main/README.de.md#42-betriebs-skripte-in-vb-api) dort listet die Skripte in einer Tabelle.

Alle Skripte werden von Hand gestartet, gehören nicht zum Request-Pfad und erwarten das `vb-api`-Projektverzeichnis als Arbeitsverzeichnis. Sie lesen ihre Konfiguration aus Umgebungsvariablen (`DATABASE_URL`, `S3_*`, ...), normalerweise derselben Datei, die der Backend-Container nutzt. Jedes Skript hat `--help`.

**Zwei Wege, ein Skript zu starten:**

- **Im Container**: in einer Shell im laufenden `vb-api`-Container (`podman exec -it vb-api bash`), wo das Arbeitsverzeichnis `/app` ist.
- **Per `podman exec`**: direkt vom Host, ohne vorher eine Shell zu öffnen. Ohne `-it` ist kein Terminal angehängt, ein Skript, das eine Frage stellt, braucht daher entweder `-it` oder seine Option `--yes` (es bricht mit einer klaren Fehlermeldung ab, statt zu hängen).

**Skripte laufen immer im Container**, denn `pg_dump`, `pg_restore` und `alembic` sind nur dort installiert, nie auf deinem eigenen Rechner.

| Skript | Zweck | Ändert Daten? |
|---|---|---|
| [`check_s3_integrity.py`](#check_s3_integritypy) | Datenbank gegen S3, in beide Richtungen | Nein |
| [`backup_db.py`](#backup_dbpy) | PostgreSQL-Dump nach S3 | Fügt einen Dump hinzu |
| [`restore_db.py`](#restore_dbpy) | Datenbank aus einem S3-Dump wiederherstellen | **Ersetzt die Datenbank** |
| [`downsync_prod.py`](#downsync_prodpy) | Nur Nicht-Produktion: Produktion spiegeln, deren neuesten Dump einspielen | **Ersetzt Daten der Stage** |
| [`maintain_deleted_archive_files.py`](#maintain_deleted_archive_filespy) | Soft-gelöschte Archivdateien auflisten, wiederherstellen und endgültig löschen | `purge*` löscht endgültig |
| [`trigger_chronicles.py`](#trigger_chroniclespy) | Probelauf oder manueller Versand der Chronik-Mail | Verschickt Mail mit `--send` |
| [Entwickler-Helfer](#entwickler-helfer) | Testlauf mit Coverage-Gate, Struktur-Prüfungen | Nein |

---

## `check_s3_integrity.py`

Nur lesende Konsistenzprüfung zwischen Datenbank und S3. Sie meldet zwei Dinge:

1. **Vollständigkeit:** Jeder `sha256_hash`, auf den eine `StandesdbImage`- oder `ArchiveStoreItem`-Zeile verweist, muss als Objekt in S3 existieren. Fehlende werden ausgegeben, der Exit-Code ist 1. Kann die Prüfung selbst nicht abgeschlossen werden (Datenbank- oder Speicherfehler), geht der Fehler nach stderr und der Exit-Code ist 2, ein kaputter Lauf wird also nie als "fehlende Dateien" gelesen.
2. **Verwaiste Objekte:** S3-Objekte unter den Bild- und Store-Präfixen, auf die gar keine Datenbank-Zeile verweist (aktiv oder soft-gelöscht), aufgelistet mit Größe, Content-Type und Änderungszeit zur manuellen Durchsicht.

Beide Prüfungen vergleichen gegen eine Sammelliste je Präfix (etwa eine Anfrage pro 1000 Objekte) statt gegen einen `head_object()`-Aufruf je Zeile; das dauert Sekunden statt vieler Minuten. Gelesen werden nur Metadaten, nie Dateiinhalte. Das Skript löscht nie etwas; verwaiste Objekte räumst du von Hand in der S3-Konsole auf.

```bash
# Im Container
python scripts/check_s3_integrity.py

# Per podman exec
podman exec vb-api python scripts/check_s3_integrity.py
```

Erwartet bei einem gesunden System: `0 missing file(s).` und `No orphaned files found.` für das Archiv und die Mitgliederdatenbank. Parameter: keine. Umgebung: `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`, `S3_PATH_STANDESDB_IMAGES`, `S3_PATH_ARCHIVE_STORE`, `DATABASE_URL`.

---

## `backup_db.py`

Schreibt einen PostgreSQL-Dump (`pg_dump`, Custom-Format) nach S3, dieselbe Operation, die der geplante Job `db_backup` jede Nacht zu `BACKUP_HOUR` ausführt (nur Produktion). Nutze es vor riskanten Änderungen, einem Umzug oder einer Restore-Probe. Die Datei heißt `<stage>-JJJJ-MM-TT_HH-MM-SS-manual.dump` (Wiener Zeit) unter dem Präfix `db-backups/`. Die Daten der Tabelle `scheduled_task_runs` bleiben bewusst draußen: Jede Stage behält ihre eigene Job-Historie.

Das Löschen von Sicherungen, die älter als `BACKUP_RETENTION_DAYS` (Standard 29) sind, ist mit `--cleanup` freiwillig, damit eine manuelle Sicherung nie nebenbei andere Sicherungen löscht.

```bash
# Per podman exec
podman exec vb-api python scripts/backup_db.py            # eine Sicherung anlegen
podman exec vb-api python scripts/backup_db.py --list     # Sicherungen auflisten, keine anlegen
podman exec vb-api python scripts/backup_db.py --cleanup  # eine anlegen, dann abgelaufene löschen
```

Erwartet: `DB backup complete: production-<zeitstempel>-manual.dump`. Umgebung: `DATABASE_URL` (muss PostgreSQL sein) und die `S3_*`-Variablen. `BACKUP_RETENTION_DAYS` zählt nur mit `--cleanup`.

---

## `restore_db.py`

Stellt die Datenbank aus einem Dump in S3 wieder her: Es lädt den Dump herunter, löscht das `public`-Schema der laufenden Datenbank und legt es neu an (andere Verbindungen werden getrennt), lädt den Dump mit `pg_restore`, prüft, dass wirklich Zeilen angekommen sind, und behält die eigene Job-Historie dieser Stage. Ohne `--backup-name` wird der neueste Dump genommen (die Namen sortieren nach Zeitstempel). Bei `APP_ENVIRONMENT=production` wird der Restore verweigert, solange `--force` fehlt, weil er die laufende Datenbank ersetzt.

```bash
podman exec vb-api python scripts/restore_db.py --list
podman exec vb-api python scripts/restore_db.py --force
podman exec vb-api python scripts/restore_db.py --force --backup-name production-2026-10-11_03-00-00.dump
```

Parameter: `--list` (Sicherungen ausgeben und beenden), `--backup-name NAME`, `--force` (in Produktion Pflicht, sonst wirkungslos). Erwartet: `Restore complete. Backup: <name>`.

**Nach einem Restore** migriert das Skript nicht. Ein Dump, der älter ist als der Code, braucht die neueren Migrationen; starte danach den Pod neu, was auch die vom Restore getrennten Verbindungen bereinigt:

```bash
podman exec vb-api alembic upgrade head
systemctl --user restart vb-api-pod.service     # auf dem Host
podman exec vb-api-pg psql -U vb -d vb -c 'SELECT count(*) FROM members;'   # erwartet: eine Zahl > 0
```

Das vollständige Vorgehen im Störfall steht im `vb-deploy`-README, [2.4](https://github.com/K-O-St-V-Vindobona-II/vb-deploy/blob/main/README.de.md#24-die-datenbank-ist-beschädigt-sicherung-und-wiederherstellung). Umgebung: `DATABASE_URL`, `APP_ENVIRONMENT`, die `S3_*`-Variablen.

---

## `downsync_prod.py`

Frischt eine **Nicht-Produktions**-Stage in zwei Schritten mit echten Produktionsdaten auf. Schritt 1 spiegelt den gesamten Produktions-AWS-S3-Bucket in das eigene Garage der Stage (eine exakte 1:1-Kopie; Objekte, die lokal, aber nicht in Produktion existieren, werden gelöscht, außer mit `--no-delete`). Schritt 2 stellt die lokale Datenbank aus dem nun aktuellen Präfix `db-backups/` des lokalen Garage wieder her und führt `alembic upgrade head` aus. Der Datenbank-Schritt spricht nie mit Produktion, er liest nur lokalen Speicher, deshalb muss Schritt 1 zuerst laufen, wenn beide aktiv sind.

Das Skript weigert sich zu laufen, wenn `APP_ENVIRONMENT=production` ist (harte Sperre, keine Übersteuerung). Es verlangt ein interaktives `yes`, bevor es etwas tut, außer mit `--yes`. Dieselbe Logik läuft nachts als geplanter Job `downsync`.

```bash
# Im Container (eine interaktive Shell hat ein Terminal)
python scripts/downsync_prod.py
python scripts/downsync_prod.py --dry-run
python scripts/downsync_prod.py --skip-db
python scripts/downsync_prod.py --skip-s3 --no-delete

# Per podman exec: eine der beiden Varianten wählen
podman exec -it vb-api python scripts/downsync_prod.py     # interaktive Bestätigung
podman exec vb-api python scripts/downsync_prod.py --yes   # nicht interaktiv
```

Parameter:

- `--dry-run`: S3-Schritt: ausgeben, was kopiert und gelöscht würde. DB-Schritt: den Dump ausgeben, der eingespielt würde, nichts einspielen.
- `--yes`: die Bestätigung überspringen.
- `--skip-db`: den Datenbank-Schritt überspringen.
- `--skip-s3`: den S3-Schritt überspringen (die Produktions-Zugangsdaten werden dann gar nicht geladen).
- `--no-delete`: nur S3-Schritt: neue und geänderte Dateien kopieren, aber lokale Waisen nicht löschen.

Umgebung: `DATABASE_URL`, `APP_ENVIRONMENT` (nicht `production`), `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET` (das lokale Garage, Ziel und Restore-Quelle). Die Produktions-Quell-Zugangsdaten sind `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` und `AWS_BUCKET`, ein nur lesender IAM-User allein für den Produktions-Bucket; wie jede Stage sie bekommt, steht im `vb-deploy`-README, [Anhang A](https://github.com/K-O-St-V-Vindobona-II/vb-deploy/blob/main/README.de.md#anhang-a-secrets-und-pflichtvariablen).

---

## `maintain_deleted_archive_files.py`

Untersucht, stellt wieder her und löscht soft-gelöschte Archivdateien endgültig. Die reguläre API löscht immer nur weich; dieses Skript ist der Weg, die Daten einer Archivdatei aus der Datenbank **und** aus S3 zu entfernen. Die einzige Ausnahme ist ein Mitglied, das seinen eigenen, noch nicht einsortierten Upload auf der Upload-Seite löscht (`DELETE /archive/upload/unfiled/{file_id}`); beide Wege teilen sich `archive_store_cleanup.hard_delete_file`. Alles andere hier ist bewusst nur per Konsole möglich und darf nie über die API oder das Frontend erreichbar werden (der Modul-Docstring von `app/services/archive_maintenance_service.py` macht das zur ausdrücklichen Regel). Das Skript läuft in **jeder** Umgebung, auch in Produktion, weil es der eigentliche Wartungsmechanismus für angesammelte soft-gelöschte Dateien ist.

Ohne Unterbefehl gibt es einen kurzen Hilfetext aus und fasst nichts an. **Alle IDs sind UUIDs.**

**S3-Auswirkung.** Mehrere Dateien (aktiv oder soft-gelöscht) können auf dasselbe S3-Objekt verweisen (Inhalts-Deduplizierung). Jede Auflistung ordnet jede Datei in ihrer Spalte `IMPACT` ein:

- **duplicate:** Eine aktive Datei verweist noch auf denselben Inhalt; ein Purge dieser Datei berührt S3 nie.
- **shared:** Keine aktive Datei, aber eine andere soft-gelöschte verweist auf denselben Inhalt; S3 bleibt vorerst unberührt, der letzte verbleibende Verweis entfernt es.
- **sole** (als `SOLE` angezeigt): Kein anderer Verweis; ein Purge löscht das S3-Objekt sofort.

**Unterbefehle**

| Unterbefehl | Wirkung |
|---|---|
| `list [dir_uuid] [--short]` | Nur lesend. Ein Absatz je Verzeichnis (Pfad und `dir_id`), danach seine Dateien mit ID, Inhalts-Hash, S3-Auswirkung, Name, Größe, Beschreibung und Löschzeit. `dir_uuid` zeigt nur ein Verzeichnis. `--short` gibt eine Zeile je Verzeichnis aus (`Pfad (dir_id: <uuid>): <anzahl>`) als schnellen Überblick. Es gibt keine Schonfrist: Jede aktuell soft-gelöschte Datei wird aufgelistet. |
| `purge <file_uuid>` | **Zerstörerisch, S3-relevant.** Prüft, dass die Datei aktuell soft-gelöscht ist (sonst Fehler, nichts gelöscht, keine Rückfrage), und verlangt dann eine getippte Bestätigung, die nennt, ob der Purge "DB only" oder "DB AND S3" betrifft. Es gibt keine Option, sie zu überspringen. Es löscht zuerst die Datenbank-Zeile endgültig (samt Kommentaren), committet und löscht erst danach das S3-Objekt und seine zwischengespeicherten Vorschaubilder, und nur, wenn keine andere Datei denselben Inhalt referenziert. Diese Reihenfolge ist Absicht: Ein fehlgeschlagenes S3-Löschen hinterlässt höchstens eine harmlose Waise, die `check_s3_integrity.py` findet, die umgekehrte Reihenfolge könnte Daten verlieren, die andere Dateien noch teilen. |
| `purge-duplicates <dir_uuid>` | **Zerstörerisch für die Datenbank, S3-sicher von Grund auf.** Löscht nur die `duplicate`-Dateien direkt in diesem Verzeichnis (nicht rekursiv) nach einer einzigen Bestätigung. Jede Datei wird direkt vor ihrem eigenen Purge neu geprüft; wurde ihr aktives Gegenstück inzwischen gelöscht, wird die Datei übersprungen und gemeldet. `shared`- und `sole`-Dateien werden nur gezählt und nie verarbeitet. |
| `restore <file_uuid>...` | Umkehrbar. Setzt `deleted_at` einer oder mehrerer soft-gelöschter Dateien zurück und macht das Soft-Delete rückgängig. Nur `SOLE`-Dateien kommen in Frage (keine andere Datei darf denselben Inhalt referenzieren); jede andere ID wird mit einer Warnung übersprungen. Läuft sofort ohne Rückfrage. |
| `list-duplicates --file <file_uuid> --dir <dir_uuid>` | Nur lesend. `--file` zeigt die aktiven Dateien, die den Inhalt mit einer Datei teilen, `--dir` dasselbe für jede soft-gelöschte Datei direkt in einem Verzeichnis. Beide Optionen lassen sich wiederholen und kombinieren; mindestens eine ist Pflicht. Eine Datei ohne Duplikate ist ein normales Ergebnis. |
| `urlpath <uuid>` | Nur lesend. Schlägt die ID als Datei und als Verzeichnis nach und gibt den Archivpfad und den Frontend-Pfad aus (`/archive/files/<uuid>` oder `/archive/dirs/<uuid>`). Fehler nur, wenn keines von beiden existiert. |

```bash
# Im Container
python scripts/maintain_deleted_archive_files.py
python scripts/maintain_deleted_archive_files.py list --short
python scripts/maintain_deleted_archive_files.py list <dir-uuid>
python scripts/maintain_deleted_archive_files.py purge <file-uuid>
python scripts/maintain_deleted_archive_files.py purge-duplicates <dir-uuid>
python scripts/maintain_deleted_archive_files.py restore <file-uuid>
python scripts/maintain_deleted_archive_files.py list-duplicates --file <file-uuid> --dir <dir-uuid>
python scripts/maintain_deleted_archive_files.py urlpath <uuid>

# Per podman exec (-it nur dort, wo eine Frage gestellt wird)
podman exec vb-api python scripts/maintain_deleted_archive_files.py list --short
podman exec -it vb-api python scripts/maintain_deleted_archive_files.py purge <file-uuid>
podman exec -it vb-api python scripts/maintain_deleted_archive_files.py purge-duplicates <dir-uuid>
podman exec vb-api python scripts/maintain_deleted_archive_files.py restore <file-uuid>
```

Eine alte Ganzzahl-ID (`purge 42`) wird mit `invalid UUID value` abgelehnt. Umgebung: `DATABASE_URL` (PostgreSQL) und die `S3_*`-Variablen.

### Runbook: Archivdateien in Produktion endgültig löschen

1. **Zuerst im Web-UI soft-löschen.** Lösche die Datei im Archiv von `vb-intern`. Das ist der umkehrbare Schritt; nur soft-gelöschte Dateien lassen sich purgen.
2. **Am Host anmelden:** `ssh service@intern.vindobona2.at`.
3. **Ansehen, was da ist** (nur lesend):
   ```bash
   # [Host] ein kurzer Überblick je Verzeichnis
   podman exec vb-api python scripts/maintain_deleted_archive_files.py list --short
   # [Host] die Dateien eines Verzeichnisses (die Verzeichnis-UUID aus dem Überblick kopieren)
   podman exec vb-api python scripts/maintain_deleted_archive_files.py list <dir-uuid>
   ```
   Die Datei-ID steht auch in der URL der Datei im Browser, `/archive/files/<uuid>`; `urlpath <uuid>` macht aus einer ID wieder ihren Archivpfad.
4. **S3-Auswirkung prüfen** in der Spalte `IMPACT`. `SOLE` heißt: Das S3-Objekt verschwindet mit dem Purge.
5. **Eine Datei purgen.** Lege vorher eine Sicherung an (`podman exec vb-api python scripts/backup_db.py`), weil sich die Datenbank-Zeile sonst nicht zurückholen lässt. `-it` ist für die Bestätigung nötig:
   ```bash
   # [Host]
   podman exec -it vb-api python scripts/maintain_deleted_archive_files.py purge <file-uuid>
   ```
   Lies die Frage: Sie nennt, ob der Purge "DB only" oder "DB AND S3" betrifft. Tippe `yes` nur, wenn du genau das willst. Für mehrere Duplikate in einem Verzeichnis nimm stattdessen `purge-duplicates <dir-uuid>`.
6. **Danach prüfen:** `list <dir-uuid>` erneut ausführen; die Datei muss weg sein.
7. **Falls du die falsche Datei gepurgt hast**, was die S3-Seite betrifft: Der Bucket ist versioniert, eine frühere Version des Objekts lässt sich in der AWS-Konsole wiederherstellen, die Datenbank-Zeile aber ist endgültig weg.

Dateien, die nicht soft-gelöscht sind, kann dieses Skript nicht purgen. Um den **eigenen, noch nicht einsortierten Upload eines Mitglieds** endgültig zu löschen, nutzt das Mitglied den Papierkorb-Button auf der Upload-Seite.

---

## `trigger_chronicles.py`

Löst die Chronik-Mail (die wöchentliche Jubiläums-Übersicht) von Hand für ein beliebiges Stichdatum aus, für eine Ende-zu-Ende-Prüfung auf Test oder Entwicklung, bevor man dem echten Dienstags-Job vertraut. Standardmäßig sicher: Ohne `--send` wird nur eine Probelauf-Zusammenfassung (Empfängerzahl und Einträge je Organisation, Status und Feld) ausgegeben, und es entsteht keine SMTP-Verbindung.

```bash
# Per podman exec
podman exec vb-api python scripts/trigger_chronicles.py --date 2026-03-31
podman exec vb-api python scripts/trigger_chronicles.py --date 2026-03-31 --send --to test@example.org
podman exec -it vb-api python scripts/trigger_chronicles.py --date 2026-03-31 --send --all-members
```

Parameter:

- `--date`: das Stichdatum (`JJJJ-MM-TT`), aus dem die Jubiläumswoche berechnet wird; Standard ist heute in der konfigurierten Zeitzone.
- `--send`: tatsächlich per SMTP zustellen. Braucht `--to` oder `--all-members`.
- `--to ADRESSE`: nur an diese eine Testadresse senden (empfohlen auf gemeinsam genutzten Test- und Entwicklungs-Stages).
- `--all-members`: mit `--send`: an **jedes Mitglied mit aktiviertem Versand** senden. Das sind echte Empfänger: Das Skript verlangt ein getipptes `yes`, das die Stage und die Empfängerzahl nennt, und braucht ein Terminal (`podman exec -it`), sonst bricht es ab.

Umgebung: `DATABASE_URL` (PostgreSQL), `APP_ENVIRONMENT` (wird in der Bestätigung gezeigt) und die `SMTP_*`-Variablen, wenn `--send` genutzt wird.

---

## Entwickler-Helfer

| Skript | Zweck |
|---|---|
| `run_pytest.sh` | Führt die Test-Suite gegen die Testdatenbank aus (aus `DATABASE_URL` abgeleitet, Datenbank `vb_test`), danach das Coverage-Gate. Wird vom Pre-Commit-Hook genutzt. `podman exec vb-api sh scripts/run_pytest.sh -q` |
| `check_coverage_gate.py` | Liest `coverage.json` des letzten Laufs und misst Statement-, Branch- und Funktions-Abdeckung gegen getrennte Mindestwerte aus `pyproject.toml` (`[tool.coverage_gate]`), damit ein Absinken einer Zahl sich nicht hinter einer anderen verstecken kann. Exit 1: unter einem Mindestwert, Exit 2: Bericht unbrauchbar. |
| `check_router_soc.py` | Schlägt fehl, wenn ein Router oder eine Dependency unter `app/api/` Geschäftslogik enthält: direkte Session-Aufrufe (`db.query()`, `db.add()`, `db.commit()`, ...) oder Importe von ORM-Modellen. Alles dieser Art gehört nach `app/services/`. |
