#!/usr/bin/env python3
"""
Extract structured dialogue data from decrypted TTX files, with audio,
background, and character expression links.

For each line of dialogue in the TTX bytecode, this script produces a
record with:

    character   — speaker name (extracted from 【name】「dialogue」)
    dialogue    — the spoken text, with character marker and quotes stripped
    koe_file    — voice-clip filename (e.g. z000100075.pcm) if one exists
    wav_path    — full path to the extracted WAV for the koe file
    background  — most recent BG{nnn} image shown at this point
    cg          — most recent character expression (立ち絵) image
    cgm         — most recent "CGM..." (event CG) image
    ita         — most recent "ITA_..." sprite
    bs          — most recent back-screen asset
    bgm         — most recent BGM track
    ev          — most recent event image (EVxx_yy)
    koe_ref     — most recent Z-prefixed voice ref in the bytecode
    full_text   — the original Shift-JIS string from the bytecode

Output formats (controlled by --csv / --json, both default-on):
    dialogue.csv              — all rows combined, sorted by scene then line
    csv_per_scene/<scene>.csv — per-scene CSV
    json_per_scene/<scene>.json — per-scene JSON

Usage:
    # default: write both CSV and JSON
    python extract_dialogue.py ttx_dir koe_dir wav_dir --out dialogue.csv

    # only JSON
    python extract_dialogue.py ttx_dir koe_dir wav_dir --no-csv --json

    # only the combined CSV (no per-scene split)
    python extract_dialogue.py ttx_dir koe_dir wav_dir --no-per-scene
"""
import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict


# Scene → priority list of koe chapters. Main story uses z0001/2/3
# (chapters 1/2/3 = Days 18/19/20). Experience mode (a01-a08) and
# saya_demo use the day-prefixed chapters z2718/2719/2720. Order matters:
# the first matching chapter wins.
SCENE_CHAPTERS = {
    'a01':       [2718, 2719, 2720, 1, 2, 3],
    'a02':       [2718, 2719, 2720, 1, 2, 3],
    'a03':       [2718, 2719, 2720, 1, 2, 3],
    'a04':       [2719, 2720, 2718, 1, 2, 3],
    'a05':       [2719, 2720, 2718, 1, 2, 3],
    'a06':       [2719, 2720, 2718, 1, 2, 3],
    'a07':       [2719, 2720, 2718, 1, 2, 3],
    'a08':       [2719, 2720, 2718, 1, 2, 3],
    'saya_demo': [2720, 2718, 2719, 1, 2, 3],
    'start':     [1, 2, 3, 2718, 2719, 2720],
    '_start':    [1, 2, 3, 2718, 2719, 2720],
    '_menu':     [1, 2, 3, 2718, 2719, 2720],
    'seen2718':     [1, 2, 3, 2718, 2719, 2720],
    'seen2718_2':   [1, 2, 3, 2718, 2719, 2720],
    'seen2719':     [2, 1, 3, 2719, 2718, 2720],
    'seen2719_2':   [2, 1, 3, 2719, 2718, 2720],
    'seen2719_3':   [2, 1, 3, 2719, 2718, 2720],
    'seen2720':     [3, 1, 2, 2720, 2718, 2719],
    'seen2720_2':   [3, 1, 2, 2720, 2718, 2719],
}

# Opcode 0x7F is "display text". Format:
#   1 byte   0x7F
#   2 bytes  text length (MojiCont) LE
#   3 bytes  script line (ScriptLine) LE — this matches the koe file's line number
#   3 bytes  read-already counter (KidokuCont) LE
#   N bytes  the Shift-JIS text itself
TEXT_OPCODE = 0x7F

# 【character】「dialogue」or【character】『dialogue』— full dialogue
CHAR_FULL_RE = re.compile(r'【(.+?)】[「『](.+?)[」』]', re.DOTALL)
# 【character】text — no quotation marks (rare)
CHAR_NOBRACKET_RE = re.compile(r'^【(.+?)】(.+)$', re.DOTALL)

# Asset name patterns — uppercase, since the bytecode stores them that way
# (the runtime calls Adv.ToLower on the result before loading the BDG file).
ASSET_PATTERNS = {
    'bg':   re.compile(r'^BG\d{3}[A-Z]?$'),         # background
    'cg':   re.compile(r'^CG[A-Z]{2}\d{1,3}[A-Z]?$'),  # character expression (face)
    'cgm':  re.compile(r'^CGM\d+$'),
    'ita':  re.compile(r'^ITA_\d{2}[A-Z]?$'),      # event CG (?)
    'bs':   re.compile(r'^BS_[A-Z]+\d+$'),
    'koe':  re.compile(r'^Z\d{10}$'),              # voice clip ref (uppercase Z)
    'bgm':  re.compile(r'^BGM\d+$'),
    'ev':   re.compile(r'^EV\d{2}_[A-Z]\d+$'),
}


def classify(s: str) -> tuple[str | None, str | None]:
    s_upper = s.upper()
    if ASSET_PATTERNS['koe'].match(s_upper):
        return 'koe', s_upper
    for cat, pat in ASSET_PATTERNS.items():
        if cat == 'koe':
            continue
        if pat.match(s_upper):
            return cat, s_upper
    return None, None


def build_koe_index(koe_dir: str) -> dict[int, list[tuple[int, str]]]:
    """{line_no: [(chapter, filename), ...]}"""
    idx: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for f in os.listdir(koe_dir):
        m = re.match(r'z(\d{4})(\d{5})\.pcm', f)
        if m:
            idx[int(m.group(2))].append((int(m.group(1)), f))
    return idx


def find_koe(scene: str, line_no: int, koe_index: dict, wav_dir: str) -> tuple[str, str]:
    cands = koe_index.get(line_no, [])
    if not cands:
        return '', ''
    prio = SCENE_CHAPTERS.get(scene, [1, 2, 3, 2718, 2719, 2720])
    cands.sort(key=lambda c: prio.index(c[0]) if c[0] in prio else 999)
    _, fname = cands[0]
    wav_path = os.path.join(wav_dir, fname.replace('.pcm', '.wav'))
    return fname, wav_path if os.path.exists(wav_path) else ''


def parse_ttx(path: str) -> tuple[list[dict], list[tuple[int, str, str]]]:
    """Return (text_entries, assets) where assets is sorted list of (offset, category, name)."""
    with open(path, 'rb') as f:
        data = f.read()

    # 1) Text entries (opcode 0x7F)
    entries = []
    i = 0
    while i < len(data):
        if data[i] != TEXT_OPCODE:
            i += 1
            continue
        if i + 9 > len(data):
            break
        text_len = data[i+1] | (data[i+2] << 8)
        line_no  = data[i+3] | (data[i+4] << 8) | (data[i+5] << 16)
        if not (2 <= text_len <= 1000) or line_no > 100_000:
            i += 1
            continue
        if i + 9 + text_len > len(data):
            break
        try:
            text = data[i+9:i+9+text_len].decode('shift_jis')
        except UnicodeDecodeError:
            i += 9 + text_len
            continue
        if not any('぀' <= c <= '鿿' for c in text):
            i += 9 + text_len
            continue
        entries.append({'offset': i, 'line_no': line_no, 'text': text})
        i += 9 + text_len

    # 2) Length-prefixed asset strings, scanned forward
    assets: list[tuple[int, str, str]] = []
    i = 0
    while i < len(data) - 1:
        n = data[i]
        if 3 <= n <= 30 and i + 1 + n <= len(data):
            try:
                s = data[i+1:i+1+n].decode('ascii')
            except UnicodeDecodeError:
                i += 1
                continue
            cat, name = classify(s)
            if cat:
                assets.append((i, cat, name))
        i += 1
    assets.sort()
    return entries, assets


def split_speaker(text: str) -> tuple[str, str]:
    m = CHAR_FULL_RE.search(text)
    if m:
        return m.group(1), m.group(2).strip()
    m = CHAR_NOBRACKET_RE.match(text)
    if m:
        return m.group(1), m.group(2).strip()
    return '', text


def build_row(scene: str, entry: dict, koe_index, wav_dir, asset_state: dict) -> dict:
    speaker, dialogue = split_speaker(entry['text'])
    koe, wav = find_koe(scene, entry['line_no'], koe_index, wav_dir)
    return {
        'scene': scene,
        'line': entry['line_no'],
        'character': speaker,
        'dialogue': dialogue,
        'koe_file': koe,
        'wav_path': wav,
        'background': asset_state.get('bg', ''),
        'cg': asset_state.get('cg', ''),
        'cgm': asset_state.get('cgm', ''),
        'ita': asset_state.get('ita', ''),
        'bs': asset_state.get('bs', ''),
        'koe_ref': asset_state.get('koe', ''),
        'bgm': asset_state.get('bgm', ''),
        'ev': asset_state.get('ev', ''),
        'full_text': entry['text'],
    }


CSV_FIELDS = [
    'scene', 'line', 'character', 'dialogue', 'koe_file', 'wav_path',
    'background', 'cg', 'cgm', 'ita', 'bs', 'koe_ref', 'bgm', 'ev',
    'full_text',
]


def write_csv(path: str, rows: list[dict]) -> None:
    with open(path, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)


def write_json(path: str, scene: str, rows: list[dict]) -> None:
    payload = {
        'scene': scene,
        'line_count': len(rows),
        'lines': rows,
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument('ttx_dir', help='Directory of XOR-decrypted .ttx files')
    ap.add_argument('koe_dir', help='Directory of original .pcm koe files')
    ap.add_argument('wav_dir', help='Directory of extracted .wav files (same names as .pcm)')

    # Output format flags. Both default-on so the user can do
    #   --no-json   to get CSV only
    #   --no-csv    to get JSON only
    ap.add_argument('--csv',  action=argparse.BooleanOptionalAction, default=True,
                    help='Write CSV output (combined + per-scene). Default: on. Use --no-csv to disable.')
    ap.add_argument('--json', action=argparse.BooleanOptionalAction, default=True,
                    help='Write per-scene JSON output. Default: on. Use --no-json to disable.')

    ap.add_argument('--out', default='dialogue.csv',
                    help='Combined CSV path (used when --csv is on). Default: dialogue.csv')
    ap.add_argument('--per-scene', default=None,
                    help='Directory for per-scene files. Default: csv_per_scene/ (CSV) and json_per_scene/ (JSON).')
    ap.add_argument('--no-per-scene', dest='per_scene_files', action='store_false', default=True,
                    help='Skip per-scene file generation (combined output only).')

    args = ap.parse_args()

    if not args.csv and not args.json:
        sys.exit('error: at least one of --csv or --json must be enabled')
    if not os.path.isdir(args.ttx_dir):
        sys.exit(f'not a directory: {args.ttx_dir}')
    if not os.path.isdir(args.koe_dir):
        sys.exit(f'not a directory: {args.koe_dir}')
    if not os.path.isdir(args.wav_dir):
        sys.exit(f'not a directory: {args.wav_dir}')

    csv_scene_dir = os.path.join(args.per_scene, 'csv') if args.per_scene else 'csv_per_scene'
    json_scene_dir = os.path.join(args.per_scene, 'json') if args.per_scene else 'json_per_scene'
    if args.per_scene_files:
        if args.csv:
            os.makedirs(csv_scene_dir, exist_ok=True)
        if args.json:
            os.makedirs(json_scene_dir, exist_ok=True)

    print(f'Indexing koe files in {args.koe_dir}…')
    koe_index = build_koe_index(args.koe_dir)
    print(f'  {sum(len(v) for v in koe_index.values())} koe files, '
          f'{len(koe_index)} unique line numbers')

    print(f'\nParsing TTX files in {args.ttx_dir}…')
    all_rows: list[dict] = []
    for fname in sorted(os.listdir(args.ttx_dir)):
        if not fname.endswith('.ttx'):
            continue
        scene = fname[:-4]
        entries, assets = parse_ttx(os.path.join(args.ttx_dir, fname))
        asset_state: dict[str, str] = {}
        ai = 0
        rows: list[dict] = []
        for e in entries:
            while ai < len(assets) and assets[ai][0] < e['offset']:
                asset_state[assets[ai][1]] = assets[ai][2]
                ai += 1
            rows.append(build_row(scene, e, koe_index, args.wav_dir, asset_state))
        if args.csv and args.per_scene_files:
            write_csv(os.path.join(csv_scene_dir, scene + '.csv'), rows)
        if args.json and args.per_scene_files:
            write_json(os.path.join(json_scene_dir, scene + '.json'), scene, rows)
        all_rows.extend(rows)
        print(f'  {scene:14s}: {len(entries):4d} entries')

    all_rows.sort(key=lambda r: (r['scene'], r['line']))

    outputs = []
    if args.csv:
        write_csv(args.out, all_rows)
        outputs.append(args.out)
        if args.per_scene_files:
            print(f'  per-scene: {csv_scene_dir}/')
    if args.json:
        json_combined = os.path.splitext(args.out)[0] + '.json'
        write_json(json_combined, 'all', all_rows)
        outputs.append(json_combined)
        if args.per_scene_files:
            print(f'  per-scene: {json_scene_dir}/')

    print(f'\nWrote {len(all_rows)} rows to {len(outputs)} file(s): {", ".join(outputs)}')
    if args.per_scene_files:
        print(f'  + per-scene {"CSV" if args.csv else ""}{" and " if args.csv and args.json else ""}'
              f'{"JSON" if args.json else ""} files')
    print(f'  with koe:    {sum(1 for r in all_rows if r["koe_file"])}')
    print(f'  with bg:     {sum(1 for r in all_rows if r["background"])}')
    print(f'  with cg:     {sum(1 for r in all_rows if r["cg"])}')
    print(f'  with bgm:    {sum(1 for r in all_rows if r["bgm"])}')
    print(f'  with character: {sum(1 for r in all_rows if r["character"])}')
    print(f'  with all (char+dialogue+koe+bg+cg+bgm): '
          f'{sum(1 for r in all_rows if r["character"] and r["dialogue"] and r["koe_file"] and r["background"] and r["cg"] and r["bgm"])}')


if __name__ == '__main__':
    main()
