RCON_PASSWORD := "changeme"
PLAYER := "FoolBar"
LEVEL := "flach1"
LEVEL_TYPE := "flat"
# Plugins von Modrinth (Slugs, kommagetrennt); lädt itzg beim Start nach data/plugins
PLUGINS := "worldedit"
SCHEMA := "haus3.json"
# Versatz zur Spielerposition (dx dy dz), z.B. "0 -1 0" = Boden bündig mit dem Gelände
OFFSET := "0 0 0"
POS_X := "-804"
POS_Y := "71"
POS_Z := "597"

# Container neu anlegen; Daten in ./data bleiben erhalten.
# Optional mit Seed:  just init 12345   (ohne Seed zufällig; wirkt nur bei neuer Welt)
init seed="":
  mkdir -p data
  -podman rm -f mc-preprod
  podman run -d --name mc-preprod \
    -e EULA=TRUE -e TYPE=PURPUR -e VERSION=LATEST \
    -e MODE=creative -e MEMORY=2G \
    -e ENABLE_RCON=true -e RCON_PASSWORD={{RCON_PASSWORD}} \
    -e OPS={{PLAYER}} \
    -e LEVEL={{LEVEL}} -e LEVEL_TYPE={{LEVEL_TYPE}} \
    -e GENERATE_STRUCTURES=false \
    -e MODRINTH_PROJECTS={{PLUGINS}} \
    {{ if seed == "" { "" } else { "-e SEED='" + seed + "'" } }} \
    -p 127.0.0.1:25565:25565 \
    -p 127.0.0.1:25575:25575 \
    -v ./data:/data:Z \
    docker.io/itzg/minecraft-server

# Welt löschen und neu erzeugen (löscht nur data/<LEVEL>*).  Optional: just reset 12345
reset seed="":
  -podman stop mc-preprod
  podman unshare rm -rf data/{{LEVEL}} data/{{LEVEL}}_nether data/{{LEVEL}}_the_end
  just init '{{seed}}'

start:
  podman start mc-preprod

stop:
  podman stop mc-preprod

restart: stop start

logs:
  podman logs -f mc-preprod

console:
  podman exec -it mc-preprod rcon-cli

# Seed der laufenden Welt anzeigen
seed:
  podman exec mc-preprod rcon-cli seed

position:
  podman exec mc-preprod rcon-cli data get entity {{PLAYER}} Pos

# Blueprint dort bauen, wo der Spieler gerade steht (Position per RCON, abgerundet).
# Anker = Ecke links vorne unten des Blueprints (Nordwest-Ecke, Haus wächst nach Osten/Süden).
#   just build                  # Blueprint aus SCHEMA
#   just build haus2.json       # anderer Blueprint
build schema=SCHEMA:
  MC_RCON_PASSWORD={{RCON_PASSWORD}} ./bp2rcon.py {{schema}} --at-player {{PLAYER}} --offset {{OFFSET}}

# Wie build, aber räumt die Baufläche vorher mit dem Axiom-Plugin (/cleararea) leer
build-clean schema=SCHEMA:
  MC_RCON_PASSWORD={{RCON_PASSWORD}} ./bp2rcon.py {{schema}} --at-player {{PLAYER}} --offset {{OFFSET}} --clear

# Wie build, aber Schritt für Schritt (Enter = nächster Schritt, q = Abbruch)
step schema=SCHEMA:
  MC_RCON_PASSWORD={{RCON_PASSWORD}} ./bp2rcon.py {{schema}} --at-player {{PLAYER}} --offset {{OFFSET}} --step

# Wie step, räumt aber vorher die Baufläche
step-clean schema=SCHEMA:
  MC_RCON_PASSWORD={{RCON_PASSWORD}} ./bp2rcon.py {{schema}} --at-player {{PLAYER}} --offset {{OFFSET}} --clear --step

# Nur anzeigen, was gebaut würde (liest die Spielerposition, setzt nichts)
dry schema=SCHEMA:
  MC_RCON_PASSWORD={{RCON_PASSWORD}} ./bp2rcon.py {{schema}} --at-player {{PLAYER}} --offset {{OFFSET}} --dry-run

# Mit festen Koordinaten bauen:  just build-at 100 70 200   |   just POS_X=-804 POS_Y=71 POS_Z=597 build-at
build-at x=POS_X y=POS_Y z=POS_Z schema=SCHEMA:
  MC_RCON_PASSWORD={{RCON_PASSWORD}} ./bp2rcon.py {{schema}} --anchor {{x}} {{y}} {{z}}

step-at x=POS_X y=POS_Y z=POS_Z schema=SCHEMA:
  MC_RCON_PASSWORD={{RCON_PASSWORD}} ./bp2rcon.py {{schema}} --anchor {{x}} {{y}} {{z}} --step
