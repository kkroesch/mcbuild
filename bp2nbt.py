#!/usr/bin/env python3
"""Blueprint-JSON -> Vanilla Structure .nbt (für /place template).

Aufruf:
  bp2nbt.py haus.json [-o haus.nbt] [--data-version N] [--max-volume 200000] [--dry-run]

Operationen (alle Koordinaten relativ zum Ursprung [0,0,0], keine negativen Werte):
  fill        {"op":"fill","from":[x,y,z],"to":[x,y,z],"block":"stein"}
  hollow_box  nur die Hülle; der Innenraum bleibt unberührt
  carve       setzt Luft (kein "block" nötig)
  setblock    {"op":"setblock","at":[x,y,z],"block":"..."}
  repeat      {"op":"repeat","count":4,"step":[3,0,0],"ops":[ ...beliebige Ops... ]}
  cylinder    {"op":"cylinder","center":[x,y,z],"radius":3,"height":10,"block":"..","hollow":true}
              center = Mitte der untersten Ebene; Achse immer senkrecht (y)
  speckle     Zufällig verstreute Blöcke: {"op":"speckle","from":[..],"to":[..],"block":"brick",
              "only":"plaster","chance":0.06,"seed":1}; ersetzt nur Blöcke vom Typ "only"
              (chance 0..0.5, Standard 0.06). Für dünne Flächen wie eine Wandseite gedacht;
              mit repeat ergibt jede Wiederholung ein anderes Muster.
  cone        Kegeldach: {"op":"cone","center":[x,y,z],"radius":4,"steep":2,"block":".."}
              center = Mitte der untersten Ebene; steep = Ebenen pro Schritt nach innen
              (Standard 2, je grösser desto steiler); "hollow": true (Standard) = nur Hülle
  pyramid     Pyramidendach: {"op":"pyramid","from":[x,y,z],"to":[x,y,z],"steep":2,"block":".."}
              Grundfläche = from/to (y von from zählt); steep wie beim Kegel
  gable_roof  Satteldach: {"op":"gable_roof","from":[x,y,z],"to":[x,y,z],"axis":"x",
              "stairs":"minecraft:spruce_stairs","ridge_block":"..","gable":".."}
              y = unterste Dachreihe (y von from/to, der kleinere Wert zählt),
              axis = Richtung des Firstes (x oder z), ridge_block und gable optional

Block-Angaben dürfen Palette-Namen oder volle Blockstates sein (mit [prop=wert,...]).
"""
import argparse
import gzip
import json
import math
import random
import re
import struct
import sys
from pathlib import Path

# --- minimaler NBT-Writer (big-endian, keine externen Abhängigkeiten) ---
T_END, T_INT, T_STRING, T_LIST, T_COMPOUND = 0, 3, 8, 9, 10


def _s(text):
    b = text.encode("utf-8")
    return struct.pack(">H", len(b)) + b


def _int(v):
    return struct.pack(">i", v)


def _named(tag, name, payload):
    return bytes([tag]) + _s(name) + payload


def _compound(fields):
    """fields: Liste aus (tag, name, payload-bytes)"""
    return b"".join(_named(*f) for f in fields) + bytes([T_END])


def _list(tag, payloads):
    return bytes([tag]) + _int(len(payloads)) + b"".join(payloads)


# ACHTUNG: an deine Serverversion anpassen (Wert für MC 26.x selbst nachschlagen).
DEFAULT_DATA_VERSION = 4189

OPS = {"fill", "hollow_box", "carve", "setblock"}  # Grundoperationen
STATE_RE = re.compile(r"^([a-z0-9_.\-:]+)(?:\[(.*)\])?$")

MAX_REPEAT = 256        # Wiederholungen pro repeat
MAX_DEPTH = 4           # Verschachtelungstiefe von repeat
MAX_PRIMS = 50_000      # Grundoperationen pro Blueprint
MAX_CELLS = 5_000_000   # Blöcke pro Blueprint


class BlueprintError(Exception):
    pass


def parse_state(s):
    """'oak_stairs[facing=north,half=bottom]' -> (name, {props})"""
    if not isinstance(s, str):
        raise BlueprintError(f"ungültiger Blockstate: {s!r}")
    m = STATE_RE.match(s.strip())
    if not m:
        raise BlueprintError(f"ungültiger Blockstate: {s!r}")
    name, props = m.group(1), {}
    if ":" not in name:
        name = "minecraft:" + name
    if m.group(2):
        for kv in m.group(2).split(","):
            if "=" not in kv:
                raise BlueprintError(f"ungültige Property in {s!r}: {kv!r}")
            k, v = kv.split("=", 1)
            props[k.strip()] = v.strip()
    return name, props


def resolve(op, palette, where):
    if op["op"] == "carve":
        return "minecraft:air"
    b = op.get("block")
    if not b:
        raise BlueprintError(f"{where}: 'block' fehlt")
    return palette.get(b, b)


def vec(op, key, where):
    v = op.get(key)
    if (not isinstance(v, list) or len(v) != 3
            or not all(isinstance(i, int) and not isinstance(i, bool) for i in v)):
        raise BlueprintError(f"{where}: '{key}' muss [x,y,z] aus Ganzzahlen sein")
    return v


# ------------------------------------------------ Zerlegung in Grundoperationen
def _shift(v, off):
    return [a + b for a, b in zip(v, off)]


def _need_int(op, key, where, lo, hi):
    v = op.get(key)
    if isinstance(v, bool) or not isinstance(v, int):
        raise BlueprintError(f"{where}: '{key}' muss eine Ganzzahl sein")
    if not lo <= v <= hi:
        raise BlueprintError(f"{where}: '{key}' muss zwischen {lo} und {hi} liegen")
    return v


def _state(block, palette, where):
    if not isinstance(block, str) or not block:
        raise BlueprintError(f"{where}: Blockangabe fehlt")
    s = palette.get(block, block)
    parse_state(s)
    return s


def _stair(state, facing):
    """Stairs-State mit erzwungener Ausrichtung; half=bottom, falls nicht angegeben."""
    name, props = parse_state(state)
    props["facing"] = facing
    props.setdefault("half", "bottom")
    return f"{name}[{','.join(f'{k}={v}' for k, v in sorted(props.items()))}]"


def _fill(a, b, block, where):
    return {"op": "fill", "from": a, "to": b, "block": block, "_src": where}


def _half_width(dx, radius):
    """Grösstes w mit dx²+w² < (radius+0.5)²; -1, wenn die Spalte leer ist."""
    lim = (radius + 0.5) ** 2 - dx * dx
    if lim <= 0:
        return -1
    return math.ceil(math.sqrt(lim)) - 1


def _cylinder(op, palette, where, off):
    cx, cy, cz = _shift(vec(op, "center", where), off)
    r = _need_int(op, "radius", where, 0, 128)
    h = _need_int(op, "height", where, 1, 256)
    state = _state(op.get("block"), palette, where)
    return _cyl_prims(cx, cy, cz, r, h, state, bool(op.get("hollow", False)), where)


def _steep(op, where):
    """Steilheit für cone/pyramid: so viele Ebenen pro Schritt nach innen (Standard 2)."""
    return _need_int({"steep": op.get("steep", 2)}, "steep", where, 1, 4)


def _cone(op, palette, where, off):
    cx, cy, cz = _shift(vec(op, "center", where), off)
    r = _need_int(op, "radius", where, 0, 64)
    steep = _steep(op, where)
    state = _state(op.get("block"), palette, where)
    hollow = bool(op.get("hollow", True))
    out = []
    for level in range((r + 1) * steep):
        rr = r - level // steep
        out += _cyl_prims(cx, cy + level, cz, rr, 1, state, hollow and rr >= 1, where)
    return out


def _pyramid(op, palette, where, off):
    f = _shift(vec(op, "from", where), off)
    t = _shift(vec(op, "to", where), off)
    lo = [min(a, b) for a, b in zip(f, t)]
    hi = [max(a, b) for a, b in zip(f, t)]
    steep = _steep(op, where)
    state = _state(op.get("block"), palette, where)
    hollow = bool(op.get("hollow", True))
    out, level = [], 0
    while level < 1024:
        inset = level // steep
        x1, x2 = lo[0] + inset, hi[0] - inset
        z1, z2 = lo[2] + inset, hi[2] - inset
        if x1 > x2 or z1 > z2:
            break
        y = lo[1] + level
        if not hollow or x2 - x1 < 2 or z2 - z1 < 2:
            out.append(_fill([x1, y, z1], [x2, y, z2], state, where))
        else:
            out.append(_fill([x1, y, z1], [x2, y, z1], state, where))
            out.append(_fill([x1, y, z2], [x2, y, z2], state, where))
            out.append(_fill([x1, y, z1 + 1], [x1, y, z2 - 1], state, where))
            out.append(_fill([x2, y, z1 + 1], [x2, y, z2 - 1], state, where))
        level += 1
    return out


def _cyl_prims(cx, cy, cz, r, h, state, hollow, where):
    hollow = hollow and r >= 1
    out = []
    for dx in range(-r, r + 1):
        w = _half_width(dx, r)
        if w < 0:
            continue
        wi = _half_width(dx, r - 1) if hollow else -1
        if wi < 0:
            segments = [(-w, w)]
        else:
            segments = [(-w, -wi - 1), (wi + 1, w)]
        for z1, z2 in segments:
            if z1 <= z2:
                out.append(_fill([cx + dx, cy, cz + z1], [cx + dx, cy + h - 1, cz + z2],
                                 state, where))
    return out


def _gable_roof(op, palette, where, off):
    f = _shift(vec(op, "from", where), off)
    t = _shift(vec(op, "to", where), off)
    lo = [min(a, b) for a, b in zip(f, t)]
    hi = [max(a, b) for a, b in zip(f, t)]
    axis = op.get("axis", "x")
    if axis not in ("x", "z"):
        raise BlueprintError(f"{where}: 'axis' muss \"x\" oder \"z\" sein")
    a_i = 0 if axis == "x" else 2   # Richtung des Firstes
    s_i = 2 - a_i                   # Richtung der Dachneigung

    stairs = _state(op.get("stairs"), palette, where)
    name, _ = parse_state(stairs)
    gable = _state(op["gable"], palette, where) if op.get("gable") else None
    if op.get("ridge_block"):
        ridge = _state(op["ridge_block"], palette, where)
    elif name.endswith("_stairs"):
        ridge = f"{name[:-len('_stairs')]}_slab[type=bottom]"
    else:
        raise BlueprintError(f"{where}: 'ridge_block' angeben (Stairs-Name endet nicht auf _stairs)")

    # facing = Richtung der Hochseite = Aufstiegsrichtung (zur Dachmitte hin)
    face_lo, face_hi = ("south", "north") if s_i == 2 else ("east", "west")

    def point(a_val, s_val, y):
        p = [0, y, 0]
        p[a_i], p[s_i] = a_val, s_val
        return p

    out = []
    for k in range((hi[s_i] - lo[s_i] + 2) // 2):
        y, p1, p2 = lo[1] + k, lo[s_i] + k, hi[s_i] - k
        if p1 < p2:
            for sv, st in ((p1, _stair(stairs, face_lo)), (p2, _stair(stairs, face_hi))):
                out.append(_fill(point(lo[a_i], sv, y), point(hi[a_i], sv, y), st, where))
        else:
            out.append(_fill(point(lo[a_i], p1, y), point(hi[a_i], p1, y), ridge, where))
        if gable and p2 - p1 >= 2:
            for av in (lo[a_i], hi[a_i]):
                out.append(_fill(point(av, p1 + 1, y), point(av, p2 - 1, y), gable, where))
    return out


def _speckle(op, palette, where, off):
    """Zufällig verstreute Einzelblöcke (z.B. Ziegel in weissen Wänden).

    Ersetzt nur Blöcke, die dort gerade 'only' sind. Reproduzierbar über 'seed'.
    Gedacht für dünne Flächen (eine Wandseite); die Zahl der Treffer ist chance*Volumen."""
    f = _shift(vec(op, "from", where), off)
    t = _shift(vec(op, "to", where), off)
    lo = [min(a, b) for a, b in zip(f, t)]
    hi = [max(a, b) for a, b in zip(f, t)]
    chance = op.get("chance", 0.06)
    if isinstance(chance, bool) or not isinstance(chance, (int, float)) or not 0 < chance <= 0.5:
        raise BlueprintError(f"{where}: 'chance' muss eine Zahl zwischen 0 und 0.5 sein")
    state = _state(op.get("block"), palette, where)
    only = _state(op.get("only"), palette, where)
    vol = 1
    for l, h in zip(lo, hi):
        vol *= h - l + 1
    n = min(vol, round(vol * chance))
    rng = random.Random(f"{op.get('seed', 0)}:{where}")
    picked = set()
    while len(picked) < n:
        picked.add(tuple(rng.randint(lo[k], hi[k]) for k in range(3)))
    return [{"op": "setblock", "at": list(p), "block": state, "only": only, "_src": where}
            for p in sorted(picked)]


def _lower_op(op, palette, where, off, depth):
    if not isinstance(op, dict):
        raise BlueprintError(f"{where}: Operation muss ein Objekt sein")
    kind = op.get("op")

    if kind in OPS:
        p = dict(op)
        for key in ("from", "to", "at"):
            if key in p:
                p[key] = _shift(vec(op, key, where), off)
        p["_src"] = where
        return [p]

    if kind == "repeat":
        if depth >= MAX_DEPTH:
            raise BlueprintError(f"{where}: repeat zu tief verschachtelt (max {MAX_DEPTH})")
        count = _need_int(op, "count", where, 1, MAX_REPEAT)
        step = vec(op, "step", where)
        sub = op.get("ops")
        if not isinstance(sub, list) or not sub:
            raise BlueprintError(f"{where}: 'ops' muss eine nicht leere Liste sein")
        out = []
        for n in range(count):
            o = [off[k] + step[k] * n for k in range(3)]
            for j, s in enumerate(sub):
                out += _lower_op(s, palette, f"{where}#{n}.ops[{j}]", o, depth + 1)
            if len(out) > MAX_PRIMS:
                raise BlueprintError(f"{where}: zu viele Teiloperationen (max {MAX_PRIMS})")
        return out

    if kind == "cylinder":
        return _cylinder(op, palette, where, off)
    if kind == "speckle":
        return _speckle(op, palette, where, off)
    if kind == "cone":
        return _cone(op, palette, where, off)
    if kind == "pyramid":
        return _pyramid(op, palette, where, off)
    if kind == "gable_roof":
        return _gable_roof(op, palette, where, off)

    raise BlueprintError(f"{where}: unbekannte Operation {kind!r}")


def lower(bp):
    """-> (Gruppen, Palette). Gruppe = (Art der Top-Level-Op, [Grundoperationen]).

    Grundoperationen sind fill/hollow_box/carve/setblock mit absolut-relativen
    Koordinaten und einem Schlüssel '_src' für Fehlermeldungen."""
    palette = bp.get("palette", {})
    if not isinstance(palette, dict):
        raise BlueprintError("'palette' muss ein Objekt sein")
    ops = bp.get("ops")
    if not isinstance(ops, list) or not ops:
        raise BlueprintError("Blueprint enthält keine Operationen")
    groups, total = [], 0
    for i, op in enumerate(ops):
        prims = _lower_op(op, palette, f"ops[{i}]", [0, 0, 0], 0)
        total += len(prims)
        if total > MAX_PRIMS:
            raise BlueprintError(f"Blueprint hat zu viele Teiloperationen (max {MAX_PRIMS})")
        groups.append((op["op"], prims))
    return groups, palette


# ----------------------------------------------------------- Blöcke berechnen
def expand(bp, max_volume):
    """Gibt {(x,y,z): blockstate-string} zurück; spätere Ops überschreiben frühere."""
    groups, palette = lower(bp)
    cells = {}
    for _kind, prims in groups:
        for op in prims:
            where, kind = op["_src"], op["op"]
            state = resolve(op, palette, where)
            parse_state(state)  # früh validieren

            if kind == "setblock":
                x, y, z = vec(op, "at", where)
                if min(x, y, z) < 0:
                    raise BlueprintError(f"{where}: negative Koordinaten nicht erlaubt")
                if op.get("only") and cells.get((x, y, z)) != op["only"]:
                    continue  # speckle: nur dort ersetzen, wo 'only' steht
                cells[(x, y, z)] = state
                continue

            a, b = vec(op, "from", where), vec(op, "to", where)
            lo = [min(p, q) for p, q in zip(a, b)]
            hi = [max(p, q) for p, q in zip(a, b)]
            if min(lo) < 0:
                raise BlueprintError(f"{where}: negative Koordinaten nicht erlaubt")
            vol = 1
            for l, h in zip(lo, hi):
                vol *= h - l + 1
            if vol > max_volume:
                raise BlueprintError(f"{where}: Volumen {vol} > max {max_volume}")

            for x in range(lo[0], hi[0] + 1):
                for y in range(lo[1], hi[1] + 1):
                    for z in range(lo[2], hi[2] + 1):
                        if kind == "hollow_box":
                            edge = (x in (lo[0], hi[0]) or y in (lo[1], hi[1])
                                    or z in (lo[2], hi[2]))
                            if not edge:
                                continue
                        cells[(x, y, z)] = state
            if len(cells) > MAX_CELLS:
                raise BlueprintError(f"Blueprint erzeugt mehr als {MAX_CELLS} Blöcke")
    if not cells:
        raise BlueprintError("Blueprint erzeugt keine Blöcke")
    return cells


def build_nbt(cells, data_version):
    size = [max(c[i] for c in cells) + 1 for i in range(3)]
    states = sorted(set(cells.values()))
    index = {s: n for n, s in enumerate(states)}

    palette = []
    for s in states:
        name, props = parse_state(s)
        fields = [(T_STRING, "Name", _s(name))]
        if props:
            fields.append((T_COMPOUND, "Properties", _compound(
                [(T_STRING, k, _s(v)) for k, v in sorted(props.items())])))
        palette.append(_compound(fields))

    blocks = []
    for (x, y, z), s in sorted(cells.items()):
        blocks.append(_compound([
            (T_LIST, "pos", _list(T_INT, [_int(x), _int(y), _int(z)])),
            (T_INT, "state", _int(index[s])),
        ]))

    root = _compound([
        (T_INT, "DataVersion", _int(data_version)),
        (T_LIST, "size", _list(T_INT, [_int(v) for v in size])),
        (T_LIST, "palette", _list(T_COMPOUND, palette)),
        (T_LIST, "blocks", _list(T_COMPOUND, blocks)),
        (T_LIST, "entities", _list(T_END, [])),
    ])
    # Root-Tag: Compound mit leerem Namen, danach gzip
    return gzip.compress(bytes([T_COMPOUND]) + _s("") + root), size


def load_blueprint(path):
    """Liest die Blueprint-JSON und erklärt, wenn stattdessen eine andere Datei übergeben wurde."""
    raw = Path(path).read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raise BlueprintError(f"{path} ist eine gzip-Datei (vermutlich eine .nbt); "
                             "erwartet wird die Blueprint-Datei im JSON-Format")
    try:
        bp = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise BlueprintError(f"{path} ist keine Textdatei; erwartet wird Blueprint-JSON")
    if not isinstance(bp, dict):
        raise BlueprintError("Blueprint muss ein JSON-Objekt sein")
    return bp


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("blueprint")
    ap.add_argument("-o", "--output")
    ap.add_argument("--data-version", type=int, default=DEFAULT_DATA_VERSION)
    ap.add_argument("--max-volume", type=int, default=200_000,
                    help="max. Blöcke pro Grundoperation (Schutz vor Rechenfehlern)")
    ap.add_argument("--dry-run", action="store_true",
                    help="nur prüfen und Statistik ausgeben")
    args = ap.parse_args()

    try:
        bp = load_blueprint(args.blueprint)
        cells = expand(bp, args.max_volume)
        nbt, size = build_nbt(cells, args.data_version)
    except (BlueprintError, json.JSONDecodeError, OSError) as e:
        sys.exit(f"Fehler: {e}")

    print(f"{bp.get('name', Path(args.blueprint).stem)}: "
          f"{len(cells)} Blöcke, Grösse {size[0]}x{size[1]}x{size[2]}")
    if args.dry_run:
        return
    out = Path(args.output or Path(args.blueprint).with_suffix(".nbt"))
    out.write_bytes(nbt)
    print(f"geschrieben: {out}")


if __name__ == "__main__":
    main()
