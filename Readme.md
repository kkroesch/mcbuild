# Blueprint-Baukasten für den Minecraft-Server

JSON-Blueprints (`rathaus.json`, `buergerhaus.json`, …) werden per RCON mit `/fill` in die Welt gebaut
(`bp2rcon.py`) oder als `.nbt` für WorldEdit/Structure-Blöcke exportiert (`bp2nbt.py`).
Alle Aufrufe laufen über `just`. Der Spieler (`PLAYER` im Justfile) muss online sein.

## `just stadt` – ganze Stadt bauen

`stadt.py` liest `stadt.json` (Gebäude mit Eingangspunkt und Richtung, Strassen, Marktplatz) und baut
Boden, Strassen, Plätze, Brunnen, Laternen und alle Gebäude in einem Lauf.

**Vorbereitung:** Stell Dich auf ebenen Boden an die **Nordwest-Ecke** der Stadt (Stadt: ca. 263 x 198 Blöcke,
x nach Osten, z nach Süden). Deine Standhöhe ist die Bodenhöhe der ganzen Stadt.

```
just stadt-map                       # Plan als Text, nichts wird gebaut
just stadt-dry                       # Befehle anzeigen, nichts senden
just stadt                           # alles bauen
just stadt --only rathaus,kirche     # nur einzelne Teile (IDs aus stadt.json)
just stadt --from gasthaus           # ab einem Teil weiter (z. B. nach Abbruch)
just stadt --skip friedhof           # Teile auslassen
just stadt --terraform               # hügeliges Gelände vorher einebnen
just stadt --step                    # Schritt für Schritt, Enter = weiter
```

Teile: `boden`, `strassen`, `gelaende` (mit `--terraform`) und jedes Gebäude per ID.
Neue Gebäude trägst Du in `stadt.json` ein (`id`, `bp`, `entry` [x,z], `facing`, optional `tag`).

## `just front` – einzelnes Gebäude vor Dir bauen

Das Gebäude entsteht **vor Dir in Blickrichtung**, sein Eingang (`"entry"` im Blueprint) zeigt zu Dir.
Höhe: Der Eingang steht auf Deiner Standhöhe, Du musst also auf Bodenhöhe stehen.

```
just front rathaus.json              # 5 Blöcke vor Dir
just front rathaus.json 12           # 12 Blöcke Abstand
just front rathaus.json 12 90        # zusätzlich um 90° gedreht (0/90/180/270)
just front rathaus.json 12 0 "0 -1 0"  # Versatz dx dy dz
just front-dry schmiede.json         # nur anzeigen
just front-step schmiede.json        # Schritt für Schritt
```

So reihst Du Häuser: Blickrichtung zur Strasse, ein Stück zurücktreten, `just front buergerhaus.json 10`,
seitlich versetzen, wiederholen. Mit Turn 180 baust Du die Gegenseite der Strasse.

## Weitere Befehle

```
just build haus2.json                # an Deiner Position (Anker = NW-Ecke des Blueprints)
just build-at 100 70 200 haus2.json  # feste Koordinaten
just backup                          # Welt sichern (Server läuft weiter)
just restore backups/<datei>.tar.gz  # Backup einspielen
```

## Dateien

| Datei | Zweck |
|---|---|
| `*.json` | Blueprints (palette, ops, entry) |
| `gen_*.py` | Generatoren für einzelne Blueprints |
| `bp2rcon.py` / `bp2nbt.py` | Blueprint → RCON-Befehle / `.nbt` |
| `stadt.py`, `stadt.json` | Stadtlayout und -bau |
| `Justfile` | Alle Aufrufe |
