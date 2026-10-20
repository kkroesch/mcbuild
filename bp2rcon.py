#!/usr/bin/env python3
"""Blueprint-JSON per RCON Schritt für Schritt auf einen Minecraft-Server bauen.

Beispiele:
  bp2rcon.py haus.json --anchor 100 64 200 --dry-run
  bp2rcon.py haus.json --anchor 100 64 200 --step
  bp2rcon.py haus.json --anchor 100 64 200 --delay 1.5 --backup-at 100 120 200
  bp2rcon.py haus.json --anchor 100 64 200 --prefix "execute at Karsten run "
  bp2rcon.py burg.json --at-player Karsten --offset 0 -6 0 --clear
  bp2rcon.py haus.json --at-player Karsten --step            # dort bauen, wo der Spieler steht
  bp2rcon.py haus.json --at-player Karsten --offset 0 -1 0   # Anker eine Zelle tiefer

--at-player liest die Position per RCON (data get entity ... Pos), rundet auf
Blockkoordinaten ab und nimmt sie als Anker; der Spieler muss online sein.
--offset verschiebt den Anker danach (gilt auch für --anchor).

Mit --front [ABSTAND] wird das Gebäude VOR den Spieler gesetzt und so gedreht, dass der
Eingang zum Spieler zeigt (Blickrichtung wird per RCON gelesen, auf 90° gerundet).
Der Eingangspunkt steht im Blueprint: "entry": {"at": [x,y,z], "facing": "south"}
(Bodenpunkt direkt vor der Tür, y = Standhöhe; facing = Richtung nach aussen).
Er landet ABSTAND Blöcke (Standard 5) genau in Blickrichtung vor dem Spieler, auf
dessen Standhöhe. Dann --offset nur noch für Feinschliff nutzen (y normalerweise 0).
  bp2rcon.py rathaus.json --at-player Karsten --front
  bp2rcon.py rathaus.json --at-player Karsten --front 12 --turn 90   # zusätzlich drehen
Ohne "entry" im Blueprint: --entry X Y Z --entry-facing north|east|south|west.

Passwort: --password oder Umgebungsvariable MC_RCON_PASSWORD.
Nutzt dasselbe JSON-Format wie bp2nbt.py (muss im selben Ordner liegen).
Ein Schritt = eine Operation aus der JSON-Datei (ein Dach oder ein repeat
besteht intern aus mehreren Befehlen, wird aber als ein Schritt ausgeführt).
"""
import argparse
import json
import math
import os
import re
import socket
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bp2nbt import (OPS, BlueprintError, load_blueprint, lower,  # noqa: E402,F401
                    parse_state, resolve, vec)

DEFAULT_MAX_FILL = 32768  # Vanilla-Grenze: gamerule commandModificationBlockLimit
ERROR_MARKERS = ("not loaded", "Unknown", "Incorrect", "Expected", "outside the world")


# ---------------------------------------------------------------- RCON
class Rcon:
    def __init__(self, host, port, password, timeout=10):
        self.sock = socket.create_connection((host, port), timeout)
        self._id = 0
        req = self._send(3, password)
        while True:
            rid, typ, _ = self._recv()
            if typ == 2:
                if rid == -1 or rid != req:
                    raise ConnectionError("RCON-Anmeldung abgelehnt (Passwort?)")
                return

    def _send(self, typ, body):
        self._id += 1
        payload = struct.pack("<ii", self._id, typ) + body.encode("utf-8") + b"\x00\x00"
        self.sock.sendall(struct.pack("<i", len(payload)) + payload)
        return self._id

    def _readn(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("RCON-Verbindung abgebrochen")
            buf += chunk
        return buf

    def _recv(self):
        (length,) = struct.unpack("<i", self._readn(4))
        data = self._readn(length)
        rid, typ = struct.unpack("<ii", data[:8])
        return rid, typ, data[8:-2].decode("utf-8", errors="replace")

    def command(self, cmd):
        self._send(2, cmd)
        return self._recv()[2]

    def close(self):
        self.sock.close()


# ------------------------------------------------------- Befehlserzeugung
def volume(lo, hi):
    v = 1
    for a, b in zip(lo, hi):
        v *= b - a + 1
    return v


def split_box(lo, hi, limit):
    """Teilt eine Box rekursiv entlang der längsten Achse, bis jedes Stück <= limit ist."""
    if volume(lo, hi) <= limit:
        return [(lo, hi)]
    ext = [h - l + 1 for l, h in zip(lo, hi)]
    ax = ext.index(max(ext))
    mid = lo[ax] + ext[ax] // 2
    hi1, lo2 = list(hi), list(lo)
    hi1[ax], lo2[ax] = mid - 1, mid
    return split_box(lo, hi1, limit) + split_box(lo2, hi, limit)


def faces(lo, hi):
    """Die sechs Aussenflächen einer Box als Einzelboxen (Kanten überlappen, harmlos)."""
    out = []
    for ax in range(3):
        for val in (lo[ax], hi[ax]):
            f_lo, f_hi = list(lo), list(hi)
            f_lo[ax] = f_hi[ax] = val
            out.append((f_lo, f_hi))
    return out



# ---------------------------------------------------- Drehung / Platzierung
DIRS = ["north", "east", "south", "west"]          # im Uhrzeigersinn, von oben gesehen
DVEC = {"north": (0, -1), "east": (1, 0), "south": (0, 1), "west": (-1, 0)}
_STATE_RE = re.compile(r"^([^\[]+)(?:\[(.*)\])?$")


def rot_xz(x, z, k):
    for _ in range(k % 4):
        x, z = -z, x                                # 90° im Uhrzeigersinn
    return x, z


def rot_state(state, k):
    """Blockstate um k*90° im Uhrzeigersinn drehen (facing, axis, rotation, Verbindungen)."""
    k %= 4
    if k == 0 or "[" not in state:
        return state
    m = _STATE_RE.match(state)
    if not m or not m.group(2):
        return state
    props = {}
    for kv in m.group(2).split(","):
        key, _, val = kv.partition("=")
        props[key] = val
    out = {}
    for key, val in props.items():
        if key == "facing" and val in DIRS:
            val = DIRS[(DIRS.index(val) + k) % 4]
        elif key == "axis" and k % 2 and val in ("x", "z"):
            val = "z" if val == "x" else "x"
        elif key == "rotation" and val.isdigit():
            val = str((int(val) + 4 * k) % 16)
        elif key in DIRS:
            key = DIRS[(DIRS.index(key) + k) % 4]
        out[key] = val
    return m.group(1) + "[" + ",".join(f"{a}={b}" for a, b in out.items()) + "]"


class Place:
    """Blueprint-Koordinate -> Weltkoordinate (Anker, optional gedreht um den Eingangspunkt)."""
    def __init__(self, origin, k=0, pivot=(0, 0, 0)):
        self.origin, self.k, self.pivot = list(origin), k % 4, pivot

    def pt(self, p):
        rx, rz = rot_xz(p[0] - self.pivot[0], p[2] - self.pivot[2], self.k)
        return [self.origin[0] + rx, self.origin[1] + p[1] - self.pivot[1], self.origin[2] + rz]

    def box(self, lo, hi):
        a, b = self.pt(lo), self.pt(hi)
        return [min(u, v) for u, v in zip(a, b)], [max(u, v) for u, v in zip(a, b)]

    def state(self, s):
        return rot_state(s, self.k)


def look_dir(rcon, name):
    """Blickrichtung (nach Yaw) als 'north'|'east'|'south'|'west'."""
    resp = rcon.command(f"data get entity {name} Rotation")
    num = r"(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)[fd]?"
    m = re.search(r"\[\s*" + num + r"\s*,\s*" + num + r"\s*\]", resp)
    if not m:
        raise RuntimeError(f"Blickrichtung von {name!r} nicht lesbar. Antwort: {resp!r}")
    yaw = float(m.group(1)) % 360                   # 0=Süden, 90=Westen, 180=Norden, 270=Osten
    return ["south", "west", "north", "east"][int(((yaw + 45) % 360) // 90)]


def make_place(pos, look, dist, entry_at, entry_facing, extra_turn, offset):
    """Eingang liegt dist Blöcke in Blickrichtung vor dem Spieler, Tür zeigt zum Spieler."""
    dx, dz = DVEC[look]
    target = [pos[0] + dx * dist + offset[0], pos[1] + offset[1], pos[2] + dz * dist + offset[2]]
    want = DIRS[(DIRS.index(look) + 2) % 4]         # Tür schaut zum Spieler
    k = (DIRS.index(want) - DIRS.index(entry_facing) + extra_turn // 90) % 4
    return Place(target, k, entry_at)


def prim_commands(op, palette, anchor, max_fill):
    """Grundoperation -> (Beschriftung, [Befehle], untere/obere Ecke absolut)."""
    where, kind = op["_src"], op["op"]
    pl = anchor if isinstance(anchor, Place) else Place(anchor)
    state = resolve(op, palette, where)
    parse_state(state)
    state = pl.state(state)

    if kind == "setblock":
        p = vec(op, "at", where)
        if min(p) < 0:
            raise BlueprintError(f"{where}: negative Koordinaten nicht erlaubt")
        a = pl.pt(p)
        if op.get("only"):
            # speckle: nur ersetzen, wo der Block gerade 'only' ist -> /fill ... replace <filter>
            return (f"setblock {p} {state} (nur wo {op['only']})",
                    [f"fill {a[0]} {a[1]} {a[2]} {a[0]} {a[1]} {a[2]} {state} replace {pl.state(op['only'])}"],
                    a, a)
        return (f"setblock {p} {state}",
                [f"setblock {a[0]} {a[1]} {a[2]} {state}"], a, a)

    f, t = vec(op, "from", where), vec(op, "to", where)
    lo = [min(a, b) for a, b in zip(f, t)]
    hi = [max(a, b) for a, b in zip(f, t)]
    if min(lo) < 0:
        raise BlueprintError(f"{where}: negative Koordinaten nicht erlaubt")
    alo, ahi = pl.box(lo, hi)

    if kind == "hollow_box":
        # 'outline': nur Hülle ersetzen, Innenraum bleibt unberührt (wie bp2nbt.py)
        if volume(alo, ahi) <= max_fill:
            pieces, mode = [(alo, ahi)], "outline"
        else:
            pieces = [q for fa in faces(alo, ahi) for q in split_box(*fa, max_fill)]
            mode = "replace"
    else:
        pieces, mode = split_box(alo, ahi, max_fill), "replace"

    cmds = [f"fill {a[0]} {a[1]} {a[2]} {b[0]} {b[1]} {b[2]} {state} {mode}"
            for a, b in pieces]
    return f"{kind} {lo}..{hi} {state}", cmds, alo, ahi


def build_steps(bp, anchor, max_fill):
    """-> Liste von (Beschreibung, [Befehle]) sowie die Gesamt-Bounding-Box (absolut)."""
    groups, palette = lower(bp)
    steps, all_lo, all_hi = [], None, None

    for kind, prims in groups:
        cmds, first_label = [], None
        for prim in prims:
            label, c, lo, hi = prim_commands(prim, palette, anchor, max_fill)
            first_label = first_label or label
            cmds += c
            all_lo = list(lo) if all_lo is None else [min(a, b) for a, b in zip(all_lo, lo)]
            all_hi = list(hi) if all_hi is None else [max(a, b) for a, b in zip(all_hi, hi)]
        if not cmds:
            continue  # z.B. Dach/Zylinder ohne Blöcke
        desc = first_label if kind in OPS else f"{kind} ({len(prims)} Teiloperationen)"
        steps.append((desc, cmds))

    if not steps:
        raise BlueprintError("Blueprint erzeugt keine Befehle")
    return steps, all_lo, all_hi


# ------------------------------------------------------------ Spielerposition
_NUM = r"(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)d?"
POS_RE = re.compile(r"\[\s*" + _NUM + r"\s*,\s*" + _NUM + r"\s*,\s*" + _NUM + r"\s*\]")


def player_block_pos(rcon, name):
    """Blockposition (abgerundet) des Spielers per 'data get entity <name> Pos'."""
    resp = rcon.command(f"data get entity {name} Pos")
    m = POS_RE.search(resp)
    if not m:
        raise RuntimeError(f"Position von {name!r} nicht lesbar (online? Name richtig?). "
                           f"Antwort des Servers: {resp!r}")
    return [math.floor(float(g)) for g in m.groups()]


# ----------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("blueprint")
    where = ap.add_mutually_exclusive_group(required=True)
    where.add_argument("--anchor", nargs=3, type=int, metavar=("X", "Y", "Z"),
                       help="Weltkoordinate des Blueprint-Ursprungs [0,0,0]")
    where.add_argument("--at-player", metavar="NAME",
                       help="Anker = Blockposition dieses Spielers (per RCON abgefragt)")
    ap.add_argument("--offset", nargs=3, type=int, default=[0, 0, 0], metavar=("DX", "DY", "DZ"),
                    help="Verschiebung des Ankers, z.B. 0 -1 0")
    ap.add_argument("--front", nargs="?", type=int, const=5, metavar="ABSTAND",
                    help="vor den Spieler bauen, Eingang zum Spieler (Standard 5 Blöcke Abstand); "
                         "braucht --at-player")
    ap.add_argument("--turn", type=int, default=0, choices=[0, 90, 180, 270],
                    help="zusätzliche Drehung zu --front (im Uhrzeigersinn)")
    ap.add_argument("--entry", nargs=3, type=int, metavar=("X", "Y", "Z"),
                    help="Eingangspunkt (überschreibt 'entry' im Blueprint)")
    ap.add_argument("--entry-facing", choices=DIRS, help="Richtung des Eingangs nach aussen")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=25575)
    ap.add_argument("--password", default=os.environ.get("MC_RCON_PASSWORD"))
    ap.add_argument("--delay", type=float, default=0.0, help="Sekunden Pause pro Schritt")
    ap.add_argument("--step", action="store_true", help="vor jedem Schritt auf Enter warten")
    ap.add_argument("--prefix", default="",
                    help='wird jedem Befehl vorangestellt, z.B. "execute at Name run " '
                         "(für Multiverse-Welten)")
    ap.add_argument("--backup-at", nargs=3, type=int, metavar=("X", "Y", "Z"),
                    help="Zielbereich: Baufläche vorher per /clone dorthin sichern")
    ap.add_argument("--clear", action="store_true",
                    help="Baufläche vorher mit dem Axiom-Plugin räumen (/cleararea ... corner sync, "
                         "läuft über die Konsole in der Hauptwelt)")
    ap.add_argument("--max-fill", type=int, default=DEFAULT_MAX_FILL,
                    help="max. Blöcke pro /fill (an gamerule commandModificationBlockLimit anpassen)")
    ap.add_argument("--dry-run", action="store_true", help="Befehle nur ausgeben, nichts senden")
    args = ap.parse_args()

    try:
        bp = load_blueprint(args.blueprint)
        build_steps(bp, [0, 0, 0], args.max_fill)  # Blueprint vorab prüfen, bevor wir verbinden
    except (BlueprintError, json.JSONDecodeError, OSError) as e:
        sys.exit(f"Fehler: {e}")

    # RCON brauchen wir zum Bauen und für --at-player (auch im Dry-Run, nur lesend)
    rcon = None
    if args.at_player or not args.dry_run:
        if not args.password:
            sys.exit("Fehler: RCON-Passwort fehlt (--password oder MC_RCON_PASSWORD)")
        try:
            rcon = Rcon(args.host, args.port, args.password)
        except (OSError, ConnectionError) as e:
            sys.exit(f"Fehler: RCON-Verbindung zu {args.host}:{args.port} fehlgeschlagen: {e}")

    try:
        anchor = args.anchor
        if args.at_player:
            try:
                anchor = player_block_pos(rcon, args.at_player)
            except RuntimeError as e:
                sys.exit(f"Fehler: {e}")
            print(f"Spieler {args.at_player} steht bei {anchor}")
        if args.front is not None:
            if not args.at_player:
                sys.exit("Fehler: --front braucht --at-player")
            ent = bp.get("entry") or {}
            e_at = args.entry or ent.get("at")
            e_face = args.entry_facing or ent.get("facing")
            if not e_at or e_face not in DIRS:
                sys.exit('Fehler: kein Eingangspunkt. Im Blueprint "entry": {"at": [x,y,z], '
                         '"facing": "south"} ergänzen oder --entry X Y Z --entry-facing ... angeben.')
            try:
                look = look_dir(rcon, args.at_player)
            except RuntimeError as e:
                sys.exit(f"Fehler: {e}")
            anchor = make_place(anchor, look, args.front, e_at, e_face, args.turn, args.offset)
            print(f"Blick nach {look}: Eingang {args.front} Blöcke vor dem Spieler bei "
                  f"{anchor.origin}, Drehung {anchor.k * 90}° (+{args.turn}°)")
        else:
            anchor = [a + o for a, o in zip(anchor, args.offset)]
            if any(args.offset):
                print(f"Anker nach Versatz {args.offset}: {anchor}")

        try:
            steps, lo, hi = build_steps(bp, anchor, args.max_fill)
        except BlueprintError as e:
            sys.exit(f"Fehler: {e}")

        total_cmds = sum(len(c) for _, c in steps)
        print(f"{bp.get('name', Path(args.blueprint).stem)}: {len(steps)} Schritte, "
              f"{total_cmds} Befehle, Bereich {lo} .. {hi}")
        print(f"WorldEdit-Auswahl danach: //pos1 {lo[0]},{lo[1]},{lo[2]}   "
              f"//pos2 {hi[0]},{hi[1]},{hi[2]}")

        backup_cmd = None
        if args.backup_at:
            if volume(lo, hi) > args.max_fill:
                sys.exit(f"Fehler: Bereich ({volume(lo, hi)} Blöcke) grösser als --max-fill; "
                         "/clone-Backup nicht möglich, bitte WorldEdit nutzen.")
            b = args.backup_at
            backup_cmd = (f"clone {lo[0]} {lo[1]} {lo[2]} {hi[0]} {hi[1]} {hi[2]} "
                          f"{b[0]} {b[1]} {b[2]}")

        clear_cmd = None
        if args.clear:
            # sync: das Plugin räumt in einem Tick, danach kann sofort gebaut werden
            clear_cmd = (f"cleararea {hi[0] - lo[0] + 1} {hi[2] - lo[2] + 1} {hi[1] - lo[1] + 1} "
                         f"corner sync {lo[0]} {lo[1]} {lo[2]}")

        if args.dry_run:
            if backup_cmd:
                print(args.prefix + backup_cmd)
            if clear_cmd:
                print("# Fläche räumen (Axiom, ohne --prefix)")
                print(clear_cmd)
            for desc, cmds in steps:
                print(f"# {desc}")
                for c in cmds:
                    print(args.prefix + c)
            return

        if backup_cmd:
            print("Backup:", rcon.command(args.prefix + backup_cmd) or "ok")
        if clear_cmd:
            resp = rcon.command(clear_cmd)
            print("Räumen:", resp or "ok")
            if "Fehler" in resp or any(m in resp for m in ERROR_MARKERS):
                sys.exit("Abbruch: /cleararea fehlgeschlagen (Axiom-Plugin installiert? "
                         "Gleiche Welt wie Hauptwelt?)")
        for n, (desc, cmds) in enumerate(steps, 1):
            if args.step:
                if input(f"[{n}/{len(steps)}] {desc}  -- Enter = ausführen, q = Abbruch: ").strip() == "q":
                    break
            else:
                print(f"[{n}/{len(steps)}] {desc}")
            for c in cmds:
                resp = rcon.command(args.prefix + c)
                if any(m in resp for m in ERROR_MARKERS):
                    sys.exit(f"Abbruch bei Schritt {n}: {resp!r}\n  Befehl: {args.prefix + c}")
                if resp:
                    print("   ", resp)
            if args.delay and not args.step:
                time.sleep(args.delay)
    finally:
        if rcon:
            rcon.close()
    print("fertig")


if __name__ == "__main__":
    main()
