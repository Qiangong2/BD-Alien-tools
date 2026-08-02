#!/usr/bin/env python3
"""
Extract dialogue and narration text from decrypted TTX files.

The TTX bytecode contains Shift-JIS string literals as operands to
"display text" opcodes. This script finds them via a simple regex
scan (good enough for the dialogue — not perfect: some kanji-only
strings and certain internal references are filtered in/out
heuristically), splits dialogue from narration, and produces a
combined ALL_TEXT.txt ordered by scene.

Usage:
    python extract_text.py <ttx_dir> --out <text_dir>

    <ttx_dir> should contain the XOR-decrypted .ttx files (output of
              extract_assets.py).
"""
import argparse
import os
import re
import sys
from pathlib import Path

# Shift-JIS: lead bytes 0x81-0x9F or 0xE0-0xFC, trail bytes 0x40-0x7E or 0x80-0xFC.
SJIS_RE = re.compile(rb'[\x81-\x9F\xE0-\xFC][\x40-\x7E\x80-\xFC]+')

# Dialogue line: 「character】「...」 or 「character」『...』.
DIALOGUE_RE = re.compile(r'^【(.+?)】(.*)$', re.DOTALL)

# Japanese sentence-end punctuation that suggests narration.
NARRATION_HINT = '。、…！？'


def has_japanese(s: str) -> bool:
    """Heuristic: does this string have hiragana/katakana or full-width brackets?"""
    for c in s:
        if '぀' <= c <= 'ゟ':  # hiragana
            return True
        if '゠' <= c <= 'ヿ':  # katakana
            return True
        if c in '「」【】『』':  # full-width brackets (likely dialogue marker)
            return True
    return False


def is_kanji_heavy(s: str) -> bool:
    """True if string is mostly kanji (might be a menu label, internal name, etc.)."""
    kanji = 0
    hirakata = 0
    for c in s:
        if '一' <= c <= '鿿':
            kanji += 1
        elif '぀' <= c <= 'ゟ' or '゠' <= c <= 'ヿ':
            hirakata += 1
    return kanji > 4 and hirakata == 0


def extract_from_ttx(path: Path) -> list[str]:
    """Find all Shift-JIS string sequences in a TTX file, filtered to readable text."""
    with open(path, 'rb') as f:
        data = f.read()
    matches = SJIS_RE.findall(data)
    out = []
    for m in matches:
        try:
            s = m.decode('shift_jis')
        except UnicodeDecodeError:
            continue
        if len(s) < 2:
            continue
        if not has_japanese(s):
            continue
        if is_kanji_heavy(s):
            # Likely a menu label / internal name. Keep it — useful for
            # understanding the game — but we won't classify it as
            # dialogue or narration.
            out.append(('other', s))
            continue
        m2 = DIALOGUE_RE.match(s)
        if m2:
            speaker = m2.group(1)
            text = m2.group(2).strip()
            if text:
                out.append(('dialogue', speaker, text))
        elif any(c in s for c in NARRATION_HINT) and len(s) > 4:
            out.append(('narration', s))
        else:
            out.append(('other', s))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('ttx_dir', type=Path,
                    help='Directory of decrypted .ttx files (output of extract_assets.py)')
    ap.add_argument('--out', type=Path, default=Path('text'),
                    help='Output directory (default: ./text)')
    args = ap.parse_args()

    ttx_dir = args.ttx_dir.resolve()
    if not ttx_dir.is_dir():
        sys.exit(f'not a directory: {ttx_dir}')
    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Process every .ttx in scene order: intros (a*), demos, then seen*.
    files = sorted(p for p in ttx_dir.iterdir() if p.suffix == '.ttx')
    if not files:
        sys.exit(f'no .ttx files in {ttx_dir}')

    total_d = total_n = total_o = 0
    per_file: list[tuple[Path, list]] = []
    for f in files:
        items = extract_from_ttx(f)
        per_file.append((f, items))
        d = sum(1 for it in items if it[0] == 'dialogue')
        n = sum(1 for it in items if it[0] == 'narration')
        o = sum(1 for it in items if it[0] == 'other')
        total_d += d
        total_n += n
        total_o += o
        # Per-file output: dialogue + narration only, with speaker labels.
        out = out_dir / (f.name + '.txt')
        with open(out, 'w', encoding='utf-8') as g:
            g.write(f'=== {f.name} ===\n')
            g.write(f'Dialogue: {d} lines, Narration: {n} lines, Other: {o}\n\n')
            current_kind = None
            current_lines: list = []
            def flush():
                if current_kind == 'dialogue':
                    g.write('--- DIALOGUE ---\n')
                    for speaker, text in current_lines:
                        g.write(f'{speaker}: {text}\n')
                elif current_kind == 'narration':
                    g.write('--- NARRATION ---\n')
                    for line in current_lines:
                        g.write(f'{line}\n')
                elif current_kind == 'other':
                    g.write('--- OTHER (labels, internal strings) ---\n')
                    for line in current_lines:
                        g.write(f'{line}\n')
                g.write('\n')
            for it in items:
                kind = it[0]
                if kind != current_kind and current_kind is not None:
                    flush()
                    current_lines = []
                current_kind = kind
                if kind == 'dialogue':
                    current_lines.append((it[1], it[2]))
                else:
                    current_lines.append(it[1])
            if current_kind is not None:
                flush()
        print(f'  {f.name}: {d} dialogue, {n} narration, {o} other')

    # Combined: ALL_TEXT.txt in scene order.
    combined = out_dir / 'ALL_TEXT.txt'
    with open(combined, 'w', encoding='utf-8') as g:
        g.write('=' * 70 + '\n')
        g.write('VABD-002 (Saya no Uta) — extracted text\n')
        g.write('Source: AUXDATA/ttx/*.ttx bytecode, Shift-JIS strings\n')
        g.write('Filter: hiragana/katakana/full-width brackets; kanji-only strings in "other"\n')
        g.write('=' * 70 + '\n\n')
        for f, items in per_file:
            g.write('\n' + '#' * 60 + '\n')
            g.write(f'# {f.name}\n')
            g.write('#' * 60 + '\n\n')
            for it in items:
                if it[0] == 'dialogue':
                    g.write(f'  {it[1]}: {it[2]}\n')
                elif it[0] == 'narration':
                    g.write(f'  {it[1]}\n')
                else:
                    g.write(f'  [{it[1]}]\n')
    print(f'\nCombined: {combined}')

    print(f'\n=== Totals ===')
    print(f'  Dialogue lines:    {total_d}')
    print(f'  Narration lines:   {total_n}')
    print(f'  Other fragments:   {total_o}')


if __name__ == '__main__':
    main()
