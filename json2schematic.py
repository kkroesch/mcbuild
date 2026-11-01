#!/usr/bin/env python3
"""Konvertiert ein Blueprint-JSON in eine Sponge-kompatible .schem-Datei."""

import argparse
import gzip
import io
import json
import struct
import sys
from pathlib import Path


# ------------------------------------------------------------- NBT Writer
def write_tag(stream, tag_id, name, payload_writer):
    stream.write(bytes([tag_id]))
    encoded_name = name.encode("utf-8")
    stream.write(struct.pack(">H", len(encoded_name)))
    stream.write(encoded_name)
    payload_writer(stream)


def write_int(stream, val):
    stream.write(struct.pack(">i", val))


def write_short(stream, val):
    stream.write(struct.pack(">h", val))


def write_string(stream, val):
    b = val.encode("utf-8")
    stream.write(struct.pack(">H", len(b)))
    stream.write(b)


def write_byte_array(stream, raw_bytes):
    stream.write(struct.pack(">i", len(raw_bytes)))
    stream.write(raw_bytes)


def write_int_array(stream, int_list):
    stream.write(struct.pack(">i", len(int_list)))
    for val in int_list:
        stream.write(struct.pack(">i", val))


def write_compound(stream, entries):
    for tag_id, name, writer in entries:
        write_tag(stream, tag_id, name, writer)
    stream.write(b"\x00")  # TAG_End


def encode_varint(val):
    res = bytearray()
    while True:
        b = val & 0x7F
        val >>= 7
        if val != 0:
            res.append(b | 0x80)
        else:
            res.append(b)
            break
    return res


# --------------------------------------------------------- JSON Converter
def json_to_schem(json_path, schem_path=None):
    with open(json_path, "r", encoding="utf-8") as f:
        bp = json.load(f)

    palette_map = bp.get("palette", {})
    ops = bp.get("ops", [])
    entry_point = bp.get("entry", {}).get("at", [0, 0, 0])

    if not ops:
        raise ValueError("JSON enthält keine Operationen ('ops').")

    # 1. Bounding-Box ermitteln
    max_x, max_y, max_z = 0, 0, 0
    for op in ops:
        if "to" in op:
            max_x = max(max_x, op["to"][0])
            max_y = max(max_y, op["to"][1])
            max_z = max(max_z, op["to"][2])
        elif "at" in op:
            max_x = max(max_x, op["at"][0])
            max_y = max(max_y, op["at"][1])
            max_z = max(max_z, op["at"][2])

    w = max_x + 1
    h = max_y + 1
    l = max_z + 1

    # 2. Raster im Speicher rendern
    grid = {}

    for op in ops:
        action = op.get("op")
        if action == "fill":
            fx, fy, fz = op["from"]
            tx, ty, tz = op["to"]
            block_alias = op["block"]
            full_block = palette_map.get(block_alias, block_alias)
            for y in range(min(fy, ty), max(fy, ty) + 1):
                for z in range(min(fz, tz), max(fz, tz) + 1):
                    for x in range(min(fx, tx), max(fx, tx) + 1):
                        grid[(x, y, z)] = full_block

        elif action == "setblock":
            x, y, z = op["at"]
            block_alias = op["block"]
            full_block = palette_map.get(block_alias, block_alias)
            grid[(x, y, z)] = full_block

        elif action == "carve":
            fx, fy, fz = op["from"]
            tx, ty, tz = op["to"]
            for y in range(min(fy, ty), max(fy, ty) + 1):
                for z in range(min(fz, tz), max(fz, tz) + 1):
                    for x in range(min(fx, tx), max(fx, tx) + 1):
                        grid[(x, y, z)] = "minecraft:air"

    # 3. Sponge-Palette und BlockData aufbauen
    used_states = sorted(set(grid.values()))
    if "minecraft:air" not in used_states:
        used_states.insert(0, "minecraft:air")
    else:
        used_states.remove("minecraft:air")
        used_states.insert(0, "minecraft:air")

    state_to_id = {state: idx for idx, state in enumerate(used_states)}

    block_data = bytearray()
    for y in range(h):
        for z in range(l):
            for x in range(w):
                state = grid.get((x, y, z), "minecraft:air")
                block_id = state_to_id[state]
                block_data.extend(encode_varint(block_id))

    # 4. Offset berechnen: In Sponge ist Offset = MinCorner - PlayerPos
    offset = [-entry_point[0], -entry_point[1], -entry_point[2]]

    # 5. NBT-Baum aufbauen
    palette_entries = []
    for state, idx in state_to_id.items():
        palette_entries.append((3, state, lambda s, v=idx: write_int(s, v)))

    schematic_entries = [
        (2, "Version", lambda s: write_short(s, 2)),
        (2, "DataVersion", lambda s: write_int(s, 2975)),  # MC 1.18+ Standard
        (2, "Width", lambda s: write_short(s, w)),
        (2, "Height", lambda s: write_short(s, h)),
        (2, "Length", lambda s: write_short(s, l)),
        (11, "Offset", lambda s: write_int_array(s, offset)),
        (10, "Palette", lambda s: write_compound(s, palette_entries)),
        (7, "BlockData", lambda s: write_byte_array(s, block_data)),
    ]

    out_file = schem_path or Path(json_path).with_suffix(".schem")
    raw_buffer = io.BytesIO()

    # Root TAG_Compound "Schematic"
    write_tag(
        raw_buffer,
        10,
        "Schematic",
        lambda s: write_compound(s, schematic_entries),
    )

    with gzip.open(out_file, "wb") as gz:
        gz.write(raw_buffer.getvalue())

    print(f"Erfolgreich erzeugt: {out_file}")
    print(
        f"Dimensionen: {w}x{h}x{l} | Blöcke: {len(state_to_id)} Typen | Offset: {offset}"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Blueprint-JSON zu .schem (WorldEdit) konvertieren"
    )
    parser.add_argument("json_file", help="Pfad zur Blueprint-JSON-Datei")
    parser.add_argument("-o", "--output", help="Ausgabedatei (.schem)")
    args = parser.parse_args()

    try:
        json_to_schem(args.json_file, args.output)
    except Exception as e:
        sys.exit(f"Fehler: {e}")


if __name__ == "__main__":
    main()