RCON_PASSWORD := "changeme"
PLAYER := "FoolBar"
LEVEL := "world"
LEVEL_TYPE := "normal"
# Plugins von Modrinth (Slugs, kommagetrennt); lädt itzg beim Start nach data/plugins
PLUGINS := "worldedit"
POS_X := "23"
POS_Y := "-60"
POS_Z := "-44"

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

build_here schema="haus3.json":
  MC_RCON_PASSWORD={{RCON_PASSWORD}} ./bp2rcon.py {{schema}} --at-player {{PLAYER}}

# Blueprint in einem Rutsch bauen
build schema="haus3.json":
  MC_RCON_PASSWORD={{RCON_PASSWORD}} ./bp2rcon.py {{schema}} --anchor {{POS_X}} {{POS_Y}} {{POS_Z}}

# Blueprint Schritt für Schritt bauen (Enter = nächster Schritt, q = Abbruch)
step schema="haus3.json":
  MC_RCON_PASSWORD={{RCON_PASSWORD}} ./bp2rcon.py {{schema}} --anchor {{POS_X}} {{POS_Y}} {{POS_Z}} --step
