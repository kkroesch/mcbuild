#!/usr/bin/env python3
"""Ganze Stadt aus Blueprints bauen: Gebäude nach Layout-Datei platzieren, Strassen, Marktplatz,
Brunnen und Laternen ergänzen. Alles über einen RCON-Zugang, Gebäude einzeln wiederholbar.

Das Layout (stadt.json) beschreibt jedes Gebäude über seinen Eingangspunkt (Blueprint-Schlüssel
"entry") in Stadtkoordinaten: Position [x,z] und Richtung, in die der Eingang zeigt.
Stadtkoordinaten: x nach Osten, z nach Süden, Ursprung = Nordwest-Ecke der Stadt.
Höhe: Standhöhe des Ankers (Spieler) = Bodenhöhe der Stadt; ebener Boden wird vorausgesetzt.

Beispiele:
  stadt.py stadt.json --map                                   # Plan als Text, nichts wird gesendet
  stadt.py stadt.json --at-player Karsten --dry-run           # Befehle zeigen
  stadt.py stadt.json --at-player Karsten                     # alles bauen (Spieler = NW-Ecke)
  stadt.py stadt.json --anchor 100 70 200 --only rathaus,kirche
  stadt.py stadt.json --at-player Karsten --from gasthaus     # ab einem Teil weiterbauen
  stadt.py stadt.json --at-player Karsten --terraform        # vorher Gelände einebnen
  stadt.py stadt.json --at-player Karsten --step              # vor jedem Teil Enter
  stadt.py stadt.json --at-player Karsten --clear             # jede Baufläche vorher mit Axiom räumen
Teile: gelaende (nur mit --terraform), boden, <Gebäude-IDs>, strassen (Strassen, Marktplatz, Brunnen, Laternen).
Passwort: MC_RCON_PASSWORD oder --password.
"""
import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bp2nbt import BlueprintError, load_blueprint  # noqa: E402
from bp2rcon import (DEFAULT_MAX_FILL, DIRS, ERROR_MARKERS, Place, Rcon,  # noqa: E402
                     build_steps, player_block_pos)

PAL = {"grass": "minecraft:grass_block", "gravel": "minecraft:gravel", "path": "minecraft:dirt_path",
       "plaza": "minecraft:stone_bricks", "cobble": "minecraft:cobblestone", "water": "minecraft:water",
       "rim": "minecraft:stone_bricks", "rimslab": "minecraft:stone_brick_slab[type=bottom]",
       "wall": "minecraft:stone_brick_wall", "lantern": "minecraft:lantern[hanging=false]"}


def greedy(cells):
    """Zellmenge {(x,z)} -> Rechtecke (x0,z0,x1,z1)."""
    left, out = set(cells), []
    for x, z in sorted(cells, key=lambda p: (p[1], p[0])):
        if (x, z) not in left:
            continue
        x2 = x
        while (x2 + 1, z) in left:
            x2 += 1
        z2 = z
        while all((xx, z2 + 1) in left for xx in range(x, x2 + 1)):
            z2 += 1
        for xx in range(x, x2 + 1):
            for zz in range(z, z2 + 1):
                left.discard((xx, zz))
        out.append((x, z, x2, z2))
    return out


def rect_cells(r):
    return {(x, z) for x in range(r[0], r[2] + 1) for z in range(r[1], r[3] + 1)}


class Town:
    def __init__(self, layout, base):
        self.layout, self.base = layout, Path(base)
        self.items = []          # (id, tag, bp, Place, lo, hi) pro Gebäude (Stadtkoordinaten)
        for b in layout["buildings"]:
            bp = load_blueprint(self.base / b["bp"])
            ent = bp.get("entry")
            if not ent:
                raise BlueprintError(f"{b['bp']}: kein 'entry' im Blueprint")
            if b["facing"] not in DIRS:
                raise BlueprintError(f"{b['id']}: facing muss {DIRS} sein")
            k = (DIRS.index(b["facing"]) - DIRS.index(ent["facing"])) % 4
            ex, ez = b["entry"]
            pl = Place([ex, 0, ez], k, ent["at"])    # Eingangspunkt (Standhöhe) liegt auf Höhe 0 = Spielerhöhe
            _, lo, hi = build_steps(bp, pl, DEFAULT_MAX_FILL)
            self.items.append({"id": b["id"], "tag": b.get("tag", b["id"][0].upper()), "bp": bp,
                               "place": pl, "lo": lo, "hi": hi, "k": k,
                               "under": b.get("underground", False)})
        xs = [i["hi"][0] for i in self.items]
        zs = [i["hi"][2] for i in self.items]
        self.W = max(xs + [r["rect"][2] for r in layout.get("streets", [])])
        self.D = max(zs + [r["rect"][3] for r in layout.get("streets", [])])
        for i in self.items:
            if i["lo"][0] < 0 or i["lo"][2] < 0:
                raise BlueprintError(f"{i['id']} ragt ins Negative (x/z {i['lo'][0]},{i['lo'][2]}): Layout nach Osten/Süden schieben")

    def overlaps(self):
        out = []
        for a in range(len(self.items)):
            for b in range(a + 1, len(self.items)):
                p, q = self.items[a], self.items[b]
                if all(p["lo"][c] <= q["hi"][c] and q["lo"][c] <= p["hi"][c] for c in (0, 2)):
                    out.append((p["id"], q["id"]))
        return out

    def building_cells(self):
        s = set()
        for i in self.items:
            s |= rect_cells((i["lo"][0], i["lo"][2], i["hi"][0], i["hi"][2]))
        return s

    def road_layers(self):
        """-> (streets, plaza, border, lamps) als Zellmengen ausserhalb von Gebäuden."""
        occ = self.building_cells()
        street = set()
        for r in self.layout.get("streets", []):
            street |= rect_cells(r["rect"])
        plaza = rect_cells(self.layout["plaza"]["rect"]) if self.layout.get("plaza") else set()
        street -= occ
        plaza -= occ
        both = street | plaza
        border = {c for c in street if any((c[0] + dx, c[1] + dz) not in both
                                          for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)))}
        return street, plaza, border

    def ground_blueprint(self):
        ops = [{"op": "fill", "from": [0, 0, 0], "to": [self.W, 0, self.D], "block": "grass"}]
        return {"name": "boden", "palette": PAL, "ops": ops}

    def terraform_blueprint(self, depth, up):
        """Gelände einebnen: oben alles abtragen, darunter Erde + Gras bis zur Standhöhe-1.
        y=0 ist depth Blöcke unter der Grasreihe; Grasreihe = y depth."""
        W, D = self.W, self.D
        ops = [{"op": "fill", "from": [0, 0, 0], "to": [W, depth - 1, D], "block": "dirt"},
               {"op": "fill", "from": [0, depth, 0], "to": [W, depth, D], "block": "grass"},
               {"op": "fill", "from": [0, depth + 1, 0], "to": [W, depth + 1 + up, D], "block": "air"}]
        pal = dict(PAL, dirt="minecraft:dirt", air="minecraft:air")
        return {"name": "gelaende", "palette": pal, "ops": ops}

    def road_blueprint(self):
        street, plaza, border = self.road_layers()
        ops = []
        rnd = random.Random(7)
        gravel = street - border
        for r in greedy(gravel):
            ops.append({"op": "fill", "from": [r[0], 0, r[1]], "to": [r[2], 0, r[3]], "block": "gravel"})
        for r in greedy(border):
            ops.append({"op": "fill", "from": [r[0], 0, r[1]], "to": [r[2], 0, r[3]], "block": "path"})
        cob = {c for c in plaza if rnd.random() < 0.18}
        for r in greedy(plaza - cob):
            ops.append({"op": "fill", "from": [r[0], 0, r[1]], "to": [r[2], 0, r[3]], "block": "plaza"})
        for r in greedy(cob):
            ops.append({"op": "fill", "from": [r[0], 0, r[1]], "to": [r[2], 0, r[3]], "block": "cobble"})
        f = self.layout.get("plaza", {}).get("fountain")
        if f:
            fx, fz = f
            ops.append({"op": "fill", "from": [fx - 3, 0, fz - 3], "to": [fx + 3, 0, fz + 3], "block": "plaza"})
            ops.append({"op": "hollow_box", "from": [fx - 3, 1, fz - 3], "to": [fx + 3, 1, fz + 3], "block": "rim"})
            ops.append({"op": "fill", "from": [fx - 2, 0, fz - 2], "to": [fx + 2, 0, fz + 2], "block": "water"})
            ops.append({"op": "setblock", "at": [fx, 0, fz], "block": "rim"})
            ops.append({"op": "fill", "from": [fx, 1, fz], "to": [fx, 3, fz], "block": "wall"})
            ops.append({"op": "setblock", "at": [fx, 4, fz], "block": "lantern"})
        lamps = sorted(c for c in border if (c[0] * 3 + c[1] * 5) % 19 == 0)
        for x, z in lamps:
            ops.append({"op": "fill", "from": [x, 1, z], "to": [x, 2, z], "block": "wall"})
            ops.append({"op": "setblock", "at": [x, 3, z], "block": "lantern"})
        if not ops:
            return None
        return {"name": "strassen", "palette": PAL, "ops": ops}

    def ascii_map(self, scale=3):
        street, plaza, border = self.road_layers()
        W, D = self.W + 1, self.D + 1
        rows = []
        for gz in range(0, D, scale):
            row = ""
            for gx in range(0, W, scale):
                ch = "."
                cells = [(x, z) for x in range(gx, min(gx + scale, W)) for z in range(gz, min(gz + scale, D))]
                for i in self.items:
                    if any(i["lo"][0] <= x <= i["hi"][0] and i["lo"][2] <= z <= i["hi"][2] for x, z in cells):
                        ch = i["tag"]
                if ch == ".":
                    if any(c in plaza for c in cells):
                        ch = "#"
                    elif any(c in street for c in cells):
                        ch = "="
                row += ch
            rows.append(row)
        return "\n".join(rows)


def tiles(lo, hi, size=128):
    """Bereich in Kacheln zerlegen (forceload erlaubt max. 256 Chunks pro Befehl)."""
    return [(x, z, min(x + size - 1, hi[0]), min(z + size - 1, hi[2]))
            for x in range(lo[0], hi[0] + 1, size) for z in range(lo[2], hi[2] + 1, size)]


def forceload(rcon, args, lo, hi, mode):
    for x0, z0, x1, z1 in tiles(lo, hi):
        cmd = f"{args.prefix}forceload {mode} {x0} {z0} {x1} {z1}"
        if args.dry_run:
            print(cmd)
            continue
        resp = rcon.command(cmd)
        if mode == "add" and ("Too many" in resp or "Unknown" in resp):
            sys.exit(f"Abbruch: forceload fehlgeschlagen: {resp!r}")


def send(rcon, args, cmd):
    """Befehl senden; wartet bei 'not loaded', bis die Chunks geladen sind."""
    for attempt in range(60):
        resp = rcon.command(args.prefix + cmd)
        if "not loaded" in resp:
            time.sleep(1.0)
            continue
        return resp
    return resp


def run_steps(rcon, steps, args, label):
    print(f"== {label}: {len(steps)} Schritte, {sum(len(c) for _, c in steps)} Befehle")
    for n, (desc, cmds) in enumerate(steps, 1):
        for c in cmds:
            if args.dry_run:
                print(args.prefix + c)
                continue
            resp = send(rcon, args, c)
            if any(m in resp for m in ERROR_MARKERS):
                sys.exit(f"Abbruch bei {label}, Schritt {n}: {resp!r}\n  Befehl: {args.prefix + c}\n"
                         f"  (nach Behebung mit --from {label} fortsetzen)")
        if args.delay and not args.dry_run:
            time.sleep(args.delay)


def hi_check(world, args):
    return world.origin[1] + args.terraform_depth + 1 + args.terraform_up


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("layout")
    w = ap.add_mutually_exclusive_group()
    w.add_argument("--anchor", nargs=3, type=int, metavar=("X", "Y", "Z"),
                   help="Weltposition der NW-Ecke der Stadt (Y = Standhöhe)")
    w.add_argument("--at-player", metavar="NAME", help="NW-Ecke der Stadt = Position des Spielers")
    ap.add_argument("--offset", nargs=3, type=int, default=[0, 0, 0], metavar=("DX", "DY", "DZ"))
    ap.add_argument("--only", help="nur diese Teile (Komma-getrennt)")
    ap.add_argument("--skip", help="diese Teile auslassen (Komma-getrennt)")
    ap.add_argument("--from", dest="start", help="ab diesem Teil weiterbauen")
    ap.add_argument("--map", action="store_true", help="Plan als Text ausgeben und beenden")
    ap.add_argument("--terraform", action="store_true",
                    help="Gelände vorher einebnen: über der Standhöhe alles abtragen, darunter mit Erde auffüllen")
    ap.add_argument("--terraform-depth", type=int, default=10, help="Erdschicht unter dem Boden (Standard 10)")
    ap.add_argument("--terraform-up", type=int, default=60, help="Höhe über dem Boden, die abgetragen wird (Standard 60)")
    ap.add_argument("--no-ground", action="store_true", help="keine Grasfläche über die ganze Stadt")
    ap.add_argument("--clear", action="store_true", help="jede Baufläche vorher mit Axiom räumen")
    ap.add_argument("--step", action="store_true", help="vor jedem Teil auf Enter warten")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-forceload", action="store_true",
                    help="Chunks nicht per /forceload laden (dann muss der Spieler alles in Sichtweite haben)")
    ap.add_argument("--keep-loaded", action="store_true", help="Chunks nach dem Bau geladen lassen")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=25575)
    ap.add_argument("--password", default=os.environ.get("MC_RCON_PASSWORD"))
    ap.add_argument("--delay", type=float, default=0.0, help="Sekunden Pause pro Schritt")
    ap.add_argument("--prefix", default="")
    ap.add_argument("--max-fill", type=int, default=DEFAULT_MAX_FILL)
    args = ap.parse_args()

    base = Path(args.layout).resolve().parent
    try:
        layout = json.loads(Path(args.layout).read_text(encoding="utf-8"))
        town = Town(layout, base)
    except (BlueprintError, OSError, json.JSONDecodeError) as e:
        sys.exit(f"Fehler: {e}")
    ov = town.overlaps()
    print(f"Stadt {layout.get('name', '')}: {len(town.items)} Gebäude, {town.W + 1} x {town.D + 1} Blöcke")
    for i in town.items:
        print(f"  {i['id']:<14} x{i['lo'][0]:>4}..{i['hi'][0]:<4} z{i['lo'][2]:>4}..{i['hi'][2]:<4} Drehung {i['k'] * 90}°")
    if ov:
        print("WARNUNG: überlappende Bereiche:", ", ".join(f"{a}/{b}" for a, b in ov))
    if args.map:
        print(town.ascii_map())
        return

    parts = []
    if args.terraform:
        parts.append(("gelaende", town.terraform_blueprint(args.terraform_depth, args.terraform_up),
                      Place([0, -1 - args.terraform_depth, 0])))
    if not args.no_ground:
        parts.append(("boden", town.ground_blueprint(), Place([0, -1, 0])))
    for i in town.items:
        parts.append((i["id"], i["bp"], i["place"]))
    rb = town.road_blueprint()
    if rb:
        parts.append(("strassen", rb, Place([0, -1, 0])))
    names = [p[0] for p in parts]
    sel = set(args.only.split(",")) if args.only else set(names)
    sel -= set(args.skip.split(",")) if args.skip else set()
    if args.start:
        if args.start not in names:
            sys.exit(f"Fehler: --from {args.start!r} unbekannt (Teile: {', '.join(names)})")
        sel &= set(names[names.index(args.start):])
    unknown = (set(args.only.split(",")) if args.only else set()) - set(names)
    if unknown:
        sys.exit(f"Fehler: unbekannte Teile {sorted(unknown)} (Teile: {', '.join(names)})")

    rcon = None
    if args.at_player or not args.dry_run:
        if not args.password:
            sys.exit("Fehler: RCON-Passwort fehlt (--password oder MC_RCON_PASSWORD)")
        try:
            rcon = Rcon(args.host, args.port, args.password)
        except (OSError, ConnectionError) as e:
            sys.exit(f"Fehler: RCON-Verbindung fehlgeschlagen: {e}")
    try:
        if args.at_player:
            ax = player_block_pos(rcon, args.at_player)
            print(f"Spieler {args.at_player} steht bei {ax}")
        elif args.anchor:
            ax = list(args.anchor)
        else:
            sys.exit("Fehler: --anchor X Y Z oder --at-player NAME angeben (oder --map)")
        ax = [a + o for a, o in zip(ax, args.offset)]
        print(f"Stadt-Ursprung (NW-Ecke, Standhöhe): {ax}")

        for name, bp, pl in parts:
            if name not in sel:
                continue
            world = Place([pl.origin[0] + ax[0], pl.origin[1] + ax[1], pl.origin[2] + ax[2]], pl.k, pl.pivot)
            if name == "gelaende" and (world.origin[1] < -64 or hi_check(world, args) > 319):
                sys.exit("Fehler: --terraform-depth/-up ragen über die Weltgrenzen (y -64..319); Werte verkleinern")
            steps, lo, hi = build_steps(bp, world, args.max_fill)
            if args.step and not args.dry_run:
                ans = input(f"[{name}] Bereich {lo}..{hi}  -- Enter = bauen, s = überspringen, q = Ende: ").strip()
                if ans == "q":
                    break
                if ans == "s":
                    continue
            if args.clear and name not in ("gelaende", "boden", "strassen"):
                cmd = (f"cleararea {hi[0] - lo[0] + 1} {hi[2] - lo[2] + 1} {hi[1] - lo[1] + 1} "
                       f"corner sync {lo[0]} {lo[1]} {lo[2]}")
                if args.dry_run:
                    print("# räumen (Axiom)\n" + cmd)
                else:
                    resp = rcon.command(cmd)
                    if "Fehler" in resp or any(m in resp for m in ERROR_MARKERS):
                        sys.exit(f"Abbruch: /cleararea für {name} fehlgeschlagen: {resp!r}")
            if not args.no_forceload:
                forceload(rcon, args, lo, hi, "add")
                if not args.dry_run:
                    time.sleep(1.5)
            try:
                run_steps(rcon, steps, args, name)
            finally:
                if not args.no_forceload and not args.keep_loaded:
                    forceload(rcon, args, lo, hi, "remove")
    finally:
        if rcon:
            rcon.close()
    print("fertig")


if __name__ == "__main__":
    main()
