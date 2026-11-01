#!/usr/bin/env python3
"""Konvertiert WorldEdit-Dateien (.schem oder .schematic) in das Blueprint-JSON-Format."""

import argparse
import gzip
import io
import json
import struct
import sys
from pathlib import Path

# Legacy Minecraft 1.12 numerische ID -> Blockname Mapping (Auswahl relevanter Blöcke)
LEGACY_IDS = {
    0: "minecraft:air",
    1: {0: "minecraft:stone", 1: "minecraft:granite", 3: "minecraft:diorite", 5: "minecraft:andesite"},
    2: "minecraft:grass_block",
    3: "minecraft:dirt",
    4: "minecraft:cobblestone",
    5: "minecraft:oak_planks",
    12: "minecraft:sand",
    13: "minecraft:gravel",
    17: "minecraft:oak_log",
    18: "minecraft:oak_leaves",
    20: "minecraft:glass",
    24: {0: "minecraft:sandstone", 1: "minecraft:chiseled_sandstone", 2: "minecraft:smooth_sandstone"},
    44: {0: "minecraft:stone_slab", 1: "minecraft:sandstone_slab", 2: "minecraft:petrified_oak_slab"},
    45: "minecraft:bricks",
    98: {0: "minecraft:stone_bricks", 1: "minecraft:mossy_stone_bricks", 2: "minecraft:cracked_stone_bricks"},
    155: {0: "minecraft:quartz_block", 1: "minecraft:chiseled_quartz_block", 2: "minecraft:quartz_pillar"},
}


# ------------------------------------------------------------- NBT Parser
def read_payload(stream, tag_id):
    if tag_id == 1:
        return struct.unpack(">b", stream.read(1))[0]
    elif tag_id == 2:
        return struct.unpack(">h", stream.read(2))[0]
    elif tag_id == 3:
        return struct.unpack(">i", stream.read(4))[0]
    elif tag_id == 4:
        return struct.unpack(">q", stream.read(8))[0]
    elif tag_id == 5:
        return struct.unpack(">f", stream.read(4))[0]
    elif tag_id == 6:
        return struct.unpack(">d", stream.read(8))[0]
    elif tag_id == 7:
        (length,) = struct.unpack(">i", stream.read(4))
        return stream.read(length)
    elif tag_id == 8:
        (length,) = struct.unpack(">H", stream.read(2))
        return stream.read(length).decode("utf-8", errors="replace")
    elif tag_id == 9:
        elem_id = stream.read(1)[0]
        (length,) = struct.unpack(">i", stream.read(4))
        return [read_payload(stream, elem_id) for _ in range(length)]
    elif tag_id == 10:
        compound = {}
        while True:
            t = stream.read(1)
            if not t or t == b"\x00":
                break
            t_id = t[0]
            (n_len,) = struct.unpack(">H", stream.read(2))
            key = stream.read(n_len).decode("utf-8", errors="replace")
            compound[key] = read_payload(stream, t_id)
        return compound
    elif tag_id == 11:
        (length,) = struct.unpack(">i", stream.read(4))
        return [struct.unpack(">i", stream.read(4))[0] for _ in range(length)]
    elif tag_id == 12:
        (length,) = struct.unpack(">i", stream.read(4))
        return [struct.unpack(">q", stream.read(8))[0] for _ in range(length)]
    raise ValueError(f"Unbekannter NBT-Tag: {tag_id}")


def read_nbt_file(filepath):
    with gzip.open(filepath, "rb") as gz:
        stream = io.BytesIO(gz.read())
    tag_type = stream.read(1)
    if not tag_type or tag_type == b"\x00":
        return {}
    (name_len,) = struct.unpack(">H", stream.read(2))
    _ = stream.read(name_len)
    return read_payload(stream, tag_type[0])


# -------------------------------------------------------- Format-Adapter
def parse_varints(raw_bytes, count):
    idx, res = 0, []
    for _ in range(count):
        val, shift = 0, 0
        while True:
            b = raw_bytes[idx]
            idx += 1
            val |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
        res.append(val)
    return res


def find_tag_recursive(data, target_key):
    """Sucht rekursiv nach einem Schlüssel in verschachtelten Dictionaries."""
    if isinstance(data, dict):
        for k, v in data.items():
            if k.lower() == target_key.lower():
                return v
            res = find_tag_recursive(v, target_key)
            if res is not None:
                return res
    elif isinstance(data, list):
        for item in data:
            res = find_tag_recursive(item, target_key)
            if res is not None:
                return res
    return None


def extract_sponge(root):
    # Palette suchen (egal auf welcher Ebene)
    palette_tag = find_tag_recursive(root, "Palette")
    if not palette_tag or not isinstance(palette_tag, dict):
        raise KeyError(
            f"Keine Palette im NBT-Baum gefunden. Vorhandene Root-Keys: {list(root.keys())}"
        )

    # Dimensionen suchen
    w = find_tag_recursive(root, "Width")
    h = find_tag_recursive(root, "Height")
    l = find_tag_recursive(root, "Length")

    if w is None or h is None or l is None:
        raise KeyError("Dimensionen (Width, Height, Length) nicht gefunden.")

    # BlockData suchen
    block_data = find_tag_recursive(root, "BlockData") or find_tag_recursive(
        root, "Data"
    )
    if block_data is None:
        raise KeyError("Kein 'BlockData'-Array gefunden.")

    id_map = {v: k for k, v in palette_tag.items()}
    indices = parse_varints(block_data, w * h * l)

    grid = {}
    for y in range(h):
        for z in range(l):
            for x in range(w):
                b_idx = (y * l + z) * w + x
                grid[(x, y, z)] = id_map.get(indices[b_idx], "minecraft:air")

    return w, h, l, grid

def extract_legacy(root):
    w, h, l = root["Width"], root["Height"], root["Length"]
    blocks = root["Blocks"]
    data = root.get("Data", b"\x00" * len(blocks))
    
    grid = {}
    for y in range(h):
        for z in range(l):
            for x in range(w):
                idx = (y * l + z) * w + x
                b_id = blocks[idx]
                meta = data[idx] & 0x0F
                
                mapping = LEGACY_IDS.get(b_id, f"minecraft:block_{b_id}")
                if isinstance(mapping, dict):
                    state = mapping.get(meta, mapping.get(0, f"minecraft:block_{b_id}"))
                else:
                    state = mapping
                grid[(x, y, z)] = state
    return w, h, l, grid


# ----------------------------------------------------- Blueprint-Builder
def convert_to_blueprint(filepath, out_path=None):
    root = read_nbt_file(filepath)
    
    if "Schematic" in root or "Palette" in root:
        w, h, l, grid = extract_sponge(root)
    elif "Blocks" in root:
        w, h, l, grid = extract_legacy(root)
    else:
        raise ValueError("Weder Sponge- noch Legacy-Schematic-Format erkannt.")

    # Palette aufbauen
    unique_states = sorted(set(grid.values()) - {"minecraft:air"})
    palette = {}
    state_to_alias = {}
    for state in unique_states:
        base = state.split("[")[0].replace("minecraft:", "")
        alias = base
        count = 1
        while alias in palette:
            alias = f"{base}_{count}"
            count += 1
        palette[alias] = state
        state_to_alias[state] = alias

    # RLE entlang X zu fill-Blöcken
    ops = []
    for y in range(h):
        for z in range(l):
            x = 0
            while x < w:
                state = grid.get((x, y, z), "minecraft:air")
                if state == "minecraft:air":
                    x += 1
                    continue
                
                start_x = x
                while x + 1 < w and grid.get((x + 1, y, z)) == state:
                    x += 1
                
                alias = state_to_alias[state]
                if start_x == x:
                    ops.append({"op": "setblock", "at": [start_x, y, z], "block": alias})
                else:
                    ops.append({"op": "fill", "from": [start_x, y, z], "to": [x, y, z], "block": alias})
                x += 1

    blueprint = {
        "name": Path(filepath).stem,
        "palette": palette,
        "ops": ops,
        "entry": {
            "at": [w // 2, 3, l - 1],
            "facing": "south"
        }
    }

    out_file = out_path or Path(filepath).with_suffix(".json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(blueprint, f, indent=1)

    print(f"Erfolgreich erzeugt: {out_file}")
    print(f"Größe: {w}x{h}x{l} | Blöcke: {len(palette)} Typen | Operationen: {len(ops)}")


def main():
    parser = argparse.ArgumentParser(description="WorldEdit-Schematic zu JSON konvertieren")
    parser.add_argument("file", help="Pfad zu .schem oder .schematic")
    parser.add_argument("-o", "--output", help="Ausgabedatei (.json)")
    args = parser.parse_args()

    try:
        convert_to_blueprint(args.file, args.output)
    except Exception as e:
        sys.exit(f"Fehler: {e}")


if __name__ == "__main__":
    main()
