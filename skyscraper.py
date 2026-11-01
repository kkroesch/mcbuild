#!/usr/bin/env python3
"""Generiert einen modernen, leicht getwisteten Post-Tower-Wolkenkratzer als Blueprint-JSON."""

import argparse
import json
import math
from pathlib import Path


def rotate_point(x, z, cx, cz, angle_rad):
    dx, dz = x - cx, z - cz
    rx = dx * math.cos(angle_rad) - dz * math.sin(angle_rad)
    rz = dx * math.sin(angle_rad) + dz * math.cos(angle_rad)
    return cx + rx, cz + rz


def generate_post_tower(floors=30, floor_height=4, lobby_height=12, twist_deg=25.0, out_path="posttower.json"):
    width = 25
    depth = 17
    cx = width / 2.0
    cz = depth / 2.0
    
    total_height = lobby_height + (floors * floor_height)
    twist_rad_total = math.radians(twist_deg)
    
    palette = {
        "glass": "minecraft:light_blue_stained_glass",
        "glass_pane": "minecraft:light_blue_stained_glass_pane",
        "iron_block": "minecraft:iron_block",
        "bars": "minecraft:iron_bars",
        "frame": "minecraft:smooth_quartz",
        "floor": "minecraft:polished_andesite",
        "accent": "minecraft:sea_lantern",
        "stairs": "minecraft:quartz_stairs[facing=north,half=bottom]",
        "rod": "minecraft:end_rod",
        "door_lo": "minecraft:iron_door[facing=south,half=lower]",
        "door_hi": "minecraft:iron_door[facing=south,half=upper]",
        "carpet": "minecraft:light_gray_carpet"
    }
    
    ops = []
    
    # 1. Fundament / Basisplatte
    pad = 4
    ops.append({
        "op": "fill",
        "from": [-pad, 2, -pad],
        "to": [width + pad, 2, depth + pad],
        "block": "floor"
    })
    
    # 2. Hohe Lobby (Höhe 3 bis lobby_height + 2)
    lobby_top = 2 + lobby_height
    ops.append({
        "op": "fill",
        "from": [0, 3, 0],
        "to": [width, lobby_top, depth],
        "block": "glass"
    })
    ops.append({
        "op": "carve",
        "from": [1, 3, 1],
        "to": [width - 1, lobby_top, depth - 1]
    })
    
    # Eckpfeiler Lobby
    for px, pz in [(0, 0), (width, 0), (0, depth), (width, depth)]:
        ops.append({
            "op": "fill",
            "from": [px, 3, pz],
            "to": [px, lobby_top, pz],
            "block": "frame"
        })
        
    # Lobby-Eingangsportal (Süden)
    mid_x = int(cx)
    ops.append({"op": "carve", "from": [mid_x - 1, 3, depth], "to": [mid_x + 1, 5, depth]})
    ops.append({"op": "setblock", "at": [mid_x - 1, 3, depth], "block": "door_lo"})
    ops.append({"op": "setblock", "at": [mid_x - 1, 4, depth], "block": "door_hi"})
    ops.append({"op": "setblock", "at": [mid_x, 3, depth], "block": "door_lo"})
    ops.append({"op": "setblock", "at": [mid_x, 4, depth], "block": "door_hi"})
    ops.append({"op": "setblock", "at": [mid_x + 1, 3, depth], "block": "door_lo"})
    ops.append({"op": "setblock", "at": [mid_x + 1, 4, depth], "block": "door_hi"})
    
    # 3. Bürotürme (Torsions-Rumpf)
    # Rasterpunkte der elliptischen Doppel-Schale
    rx = width / 2.0 - 0.5
    rz = depth / 2.0 - 0.5
    
    shell_coords = set()
    for ix in range(width + 1):
        for iz in range(depth + 1):
            nx = (ix - cx) / rx
            nz = (iz - cz) / rz
            dist_sq = nx * nx + nz * nz
            # Hohle Schale mit Atrium-Lücke in der Mitte (Z-Achse geteilt)
            if 0.70 <= dist_sq <= 1.05:
                shell_coords.add((ix, iz))
                
    curr_y = lobby_top + 1
    
    for f in range(floors):
        fraction = f / float(max(1, floors - 1))
        angle = fraction * twist_rad_total
        floor_y = curr_y
        next_floor_y = floor_y + floor_height
        
        # Geschossdecke & Randträger
        # Passerellen / Brücken alle 3 Stockwerke
        has_catwalk = (f % 3 == 0)
        
        # Außenhaut für dieses Stockwerk
        for y in range(floor_y, next_floor_y):
            is_slab_level = (y == floor_y)
            for bx, bz in shell_coords:
                rx_rot, rz_rot = rotate_point(bx, bz, cx, cz, angle)
                gx = int(round(rx_rot))
                gz = int(round(rz_rot))
                
                block_type = "iron_block" if is_slab_level else "glass"
                ops.append({"op": "setblock", "at": [gx, y, gz], "block": block_type})
                
        # Passerelle (Verbindungssteg zwischen Ost- und Westflügel)
        if has_catwalk and f > 0:
            bridge_width = 2
            for bx in range(int(cx) - bridge_width, int(cx) + bridge_width + 1):
                for bz in range(2, depth - 1):
                    rx_rot, rz_rot = rotate_point(bx, bz, cx, cz, angle)
                    gx = int(round(rx_rot))
                    gz = int(round(rz_rot))
                    ops.append({"op": "setblock", "at": [gx, floor_y, gz], "block": "frame"})
                    if bx in (int(cx) - bridge_width, int(cx) + bridge_width):
                        ops.append({"op": "setblock", "at": [gx, floor_y + 1, gz], "block": "bars"})
                        
            # Beleuchtung an der Passerelle
            lx_rot, lz_rot = rotate_point(cx, cz, cx, cz, angle)
            ops.append({"op": "setblock", "at": [int(round(lx_rot)), floor_y, int(round(lz_rot))], "block": "accent"})
            
        # Nottreppen-Segment im Kern
        for ty in range(floor_y, next_floor_y):
            ops.append({"op": "setblock", "at": [int(cx) - 1, ty, int(cz) - 1], "block": "frame"})
            ops.append({"op": "setblock", "at": [int(cx), ty, int(cz) - 1], "block": "stairs"})
            
        curr_y = next_floor_y
        
    roof_y = curr_y
    
    # 4. Dachabschluss & Helipad / Technik
    final_angle = twist_rad_total
    for bx in range(width + 1):
        for bz in range(depth + 1):
            nx = (bx - cx) / rx
            nz = (bz - cz) / rz
            if nx * nx + nz * nz <= 0.85:
                rx_rot, rz_rot = rotate_point(bx, bz, cx, cz, final_angle)
                ops.append({"op": "setblock", "at": [int(round(rx_rot)), roof_y, int(round(rz_rot))], "block": "iron_block"})
                
    # 5. Große Sendeantenne (Höhe 18 Blöcke über Dach)
    acx_rot, acz_rot = rotate_point(cx, cz, cx, cz, final_angle)
    ax, az = int(round(acx_rot)), int(round(acz_rot))
    
    # Antennen-Basis
    ops.append({"op": "fill", "from": [ax - 1, roof_y + 1, az - 1], "to": [ax + 1, roof_y + 2, az + 1], "block": "frame"})
    # Mast
    ops.append({"op": "fill", "from": [ax, roof_y + 3, az], "to": [ax, roof_y + 14, az], "block": "bars"})
    # Spitze
    ops.append({"op": "fill", "from": [ax, roof_y + 15, az], "to": [ax, roof_y + 18, az], "block": "rod"})
    ops.append({"op": "setblock", "at": [ax, roof_y + 19, az], "block": "accent"})
    
    # Normalisierung: Keine negativen Koordinaten
    min_x = min(op["from"][0] if "from" in op else op["at"][0] for op in ops)
    min_z = min(op["from"][2] if "from" in op else op["at"][2] for op in ops)
    
    shift_x = -min_x if min_x < 0 else 0
    shift_z = -min_z if min_z < 0 else 0
    
    if shift_x > 0 or shift_z > 0:
        for op in ops:
            if "from" in op:
                op["from"][0] += shift_x
                op["from"][2] += shift_z
                op["to"][0] += shift_x
                op["to"][2] += shift_z
            elif "at" in op:
                op["at"][0] += shift_x
                op["at"][2] += shift_z

    blueprint = {
        "name": "posttower_bonn",
        "palette": palette,
        "ops": ops,
        "entry": {
            "at": [mid_x + shift_x, 3, depth + shift_z + 2],
            "facing": "south"
        }
    }
    
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(blueprint, f, indent=1)
        
    print(f"Erzeugt: {out_path} ({len(ops)} Operationen, Gesamthöhe ca. {roof_y + 20} Blöcke, Twist: {twist_deg}°)")


def main():
    parser = argparse.ArgumentParser(description="Post-Tower Generator für bp2rcon")
    parser.add_argument("--floors", type=int, default=32, help="Anzahl Stockwerke (Standard 32)")
    parser.add_argument("--floor-height", type=int, default=4, help="Höhe je Stockwerk (Standard 4)")
    parser.add_argument("--lobby", type=int, default=12, help="Höhe der Eingangshalle (Standard 12)")
    parser.add_argument("--twist", type=float, default=20.0, help="Verdrehung über Gesamthöhe in Grad (Standard 20.0)")
    parser.add_argument("-o", "--output", default="posttower.json", help="Ausgabedatei")
    args = parser.parse_args()
    
    generate_post_tower(
        floors=args.floors,
        floor_height=args.floor_height,
        lobby_height=args.lobby,
        twist_deg=args.twist,
        out_path=args.output
    )


if __name__ == "__main__":
    main()