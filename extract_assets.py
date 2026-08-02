#!/usr/bin/env python3
"""
Extract assets from VABD-002 (Saya no Uta) BD-J disc.

Nitroplus's TauSystem engine uses several proprietary formats. The JAR
(TauSystem/) contains no decoder for .pcm (BCLK) audio — that's a custom
BD-J plugin on the player — but it does contain the XOR tables needed to
decrypt the other formats, all stored as static initializers in the .class
files and extractable with javap.

Formats handled:
  - .bdg:  image (PNG/JPEG/GIF), 76-byte header + XOR body (FileIO.BDGAngoTbl, 4096B)
  - .fnt:  font (PNG/JPEG/GIF), 936-byte header + XOR body (FileIO.FNTAngoTbl, 512B)
  - .ttx:  game script bytecode, 32-byte header + XOR body (Script.TTXAngo, 512B)
            structure: u32 AlreadCount, u32 ZLabelCount, [u32 ZLabelNo]*N,
            [u32 ZLabelAddr]*N, u32 ZLabelLen, [byte ScriptData; XOR'd from
            offset (ScriptData.length & 0x1FF)].
            The bytecode contains the actual game text (Shift-JIS strings
            like "「そ、それでは…」" embedded as operands to display-text
            opcodes). Run `extract_text.py` on the decrypted TTX to pull
            the dialogue out — 3,771 dialogue + 3,219 narration lines
            across 16 scripts.
  - .pcm:  audio in BCLK0200 format, 56-byte Nitroplus header wrapping
            16-bit BIG-ENDIAN PCM. NO XOR — the body is plain LPCM. The
            Sound class just hands the file URL to javax.media.Manager
            .createPlayer, so the player's built-in LPCM handler does the
            decode (BD-J mandatory codec). On a standard PC the byte order
            was the trap: interpreting BE data as LE produces static because
            every 16-bit word has its bytes swapped. Extracted as 48 kHz
            (BD-J mandatory) with channels inferred from the directory
            (koe/ = mono voice, pcm/ = stereo BGM).
  - .dat / .tbl: cglist.tbl, _cg_taiken.dat, _mem_taiken.dat, gameexe.dat
            use their own small formats and are passed through with a
            short extension rename so we can tell which is which.
"""
import argparse
import os
import struct
import sys
import wave
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_WORKSPACE = _HERE.parent

def _find_table(name: str) -> Path:
    return next(
        (p for p in (_HERE / name, _WORKSPACE / name) if p.exists()),
        _HERE / name,
    )

BDG_XOR_TABLE_PATH = _find_table('bdg_angotbl.bin')
FNT_XOR_TABLE_PATH = _find_table('fnt_angotbl.bin')
TTX_XOR_TABLE_PATH = _find_table('ttx_angotbl.bin')


def load_xor_table(path: Path) -> bytes:
    with open(path, 'rb') as f:
        return f.read()


def _detect_image_format(data: bytes) -> str | None:
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'png'
    if data[:3] == b'\xff\xd8\xff':
        return 'jpg'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    return None


def extract_bdg(src: Path, out_dir: Path, xor_table: bytes) -> int:
    """Decrypt a .bdg file and write the embedded image (PNG/JPG/GIF).

    Header is 76 bytes, little-endian:
      0x00-0x03: 'BDG\\0'
      0x04-0x07: 0
      0x08-0x0B: version=1
      0x0C-0x0F: sub_version=0
      0x10-0x13: HSize+4 (= 60, but the actual header is 76 bytes)
      0x14-0x17: HSize echo (= 76, the read uses 56 bytes here)
      0x18-0x1B: XSize (width)
      0x1C-0x1F: YSize (height)
      0x20-0x23: button count (always 0 in this disc)
      0x24-0x27: GrpSize (image data size, = file_size - 76)
      0x28-0x37: padding / button records (zero in this disc)
      0x38-0x4B: start of image body (also the first 8 bytes of audio data
                   for pcm; bdg just starts the image right after the header)
    The body is XOR-encrypted: byte at offset i is XORed with
    BDGAngoTbl[(file_size & 0xFFF) + i] (clamped to table size).
    """
    with open(src, 'rb') as f:
        data = f.read()
    if data[:4] != b'BDG\0':
        raise ValueError(f'{src}: bad magic (expected BDG\\0, got {data[:4]!r})')
    if data[8:12] != b'\x01\x00\x00\x00':
        raise ValueError(f'{src}: bad version (expected 1)')
    hsize = struct.unpack_from('<I', data, 16)[0] - 4
    xs = struct.unpack_from('<I', data, hsize + 8)[0]
    ys = struct.unpack_from('<I', data, hsize + 12)[0]
    grp_size = struct.unpack_from('<I', data, hsize + 20)[0]

    body = bytearray(data[20 + hsize:20 + hsize + grp_size])
    pt = len(data) & 0xFFF
    for i in range(len(body)):
        body[i] ^= xor_table[pt & 0xFFF]
        pt += 1

    fmt = _detect_image_format(bytes(body))
    if fmt is None:
        out = out_dir / (src.stem + '.bin')
        with open(out, 'wb') as f:
            f.write(body)
        print(f'  WARN: {src.name} -> {out.name} (unknown image format, {xs}x{ys})')
        return 0

    out = out_dir / f'{src.stem}.{fmt}'
    with open(out, 'wb') as f:
        f.write(body)
    print(f'  {src.name} -> {out.name} ({xs}x{ys}, {len(body)} bytes)')
    return 0


def extract_fnt(src: Path, out_dir: Path, xor_table: bytes) -> int:
    """Decrypt a .fnt file. 936-byte header (skipped) + XOR body."""
    with open(src, 'rb') as f:
        data = f.read()
    if data[:4] != b'FNT\x00':
        raise ValueError(f'{src}: bad magic (expected FNT\\0)')

    body = bytearray(data[936:])
    pt = 0
    for i in range(len(body)):
        body[i] ^= xor_table[pt & 0x1FF]
        pt += 1

    fmt = _detect_image_format(bytes(body))
    if fmt is None:
        out = out_dir / (src.stem + '.bin')
        with open(out, 'wb') as f:
            f.write(body)
        print(f'  WARN: {src.name} -> {out.name} (unknown image format)')
        return 0

    out = out_dir / f'{src.stem}.{fmt}'
    with open(out, 'wb') as f:
        f.write(body)
    print(f'  {src.name} -> {out.name} ({len(body)} bytes)')
    return 0


def extract_ttx(src: Path, out_dir: Path, xor_table: bytes) -> int:
    """Decrypt a .ttx game-script file.

    File layout (the 32-byte TTX magic header is what FileIO.UnpackTTX
    strips before the Script class sees the body):
      u32 AlreadCount
      u32 ZLabelCount
      u32 ZLabelNo[ZLabelCount]    (label numbers)
      u32 ZLabelAddr[ZLabelCount]  (label offsets within ScriptData)
      u32 ZLabelLen
      byte ScriptData[ZLabelLen]   (XOR'd, decrypt from offset
                                     ScriptData.length & 0x1FF)
    The output is the decrypted ScriptData only — the script bytecode that
    the TauSystem Script class interprets. It contains length-prefixed
    strings referencing game assets (BG002, S[0], voice clip names) and
    control opcodes; the actual story text lives in the koe/ voice files.
    """
    with open(src, 'rb') as f:
        data = f.read()

    # Strip the 32-byte TTX magic header (same as FileIO.UnpackTTX).
    if data[:4] == b'TTX\x00':
        data = data[32:]

    if len(data) < 84:
        # Not a ttx (probably one of the .dat/.tbl files misidentified as ttx).
        out = out_dir / src.name
        with open(out, 'wb') as f:
            f.write(data)
        print(f'  {src.name} -> {out.name} (passthrough, non-ttx format)')
        return 0

    alread_count = struct.unpack_from('<I', data, 0)[0]
    zlabel_count = struct.unpack_from('<I', data, 4)[0]
    expected_header = 8 + 4 * (zlabel_count * 2 + 1)
    if expected_header > len(data) or zlabel_count > 10000 or alread_count > 1000000:
        # Not a ttx (probably one of the .dat/.tbl files misidentified as ttx).
        out = out_dir / src.name
        with open(out, 'wb') as f:
            f.write(data)
        print(f'  {src.name} -> {out.name} (passthrough, non-ttx format)')
        return 0

    pos = 8 + 4 * (zlabel_count * 2)
    zlabel_len = struct.unpack_from('<I', data, pos)[0]
    pos += 4
    if pos + zlabel_len > len(data) or zlabel_len > len(data):
        # _cg_taiken.dat and similar files have plausible-looking header counts
        # but bogus ZLabelLen / ZLabelNo values; treat them as passthrough.
        out = out_dir / src.name
        with open(out, 'wb') as f:
            f.write(data)
        print(f'  {src.name} -> {out.name} (passthrough, invalid ttx structure)')
        return 0

    script_data = bytearray(data[pos:pos + zlabel_len])
    start_offset = len(script_data) & 0x1FF
    for i in range(len(script_data)):
        script_data[i] ^= xor_table[(start_offset + i) & 0x1FF]

    out = out_dir / src.name
    with open(out, 'wb') as f:
        f.write(script_data)
    print(f'  {src.name} -> {out.name} ({len(script_data)} bytes, AlreadCount={alread_count}, ZLabelCount={zlabel_count})')
    return 0


def parse_bclk_header(data: bytes) -> dict:
    """Parse the 56-byte Nitroplus BCLK header.

    Known fields (all big-endian):
      0x00-0x07: "BCLK0200" magic
      0x08-0x0B: header_size + 4 (always 0x38 = 56)
      0x0C-0x0F: 0 for pcm/BGM; full file size for koe/voice (inconsistent —
                  likely just a per-file-type flag, not a header field)
      0x28-0x2B: 12 (constant — likely "channels-related" or "format id 1")
      0x2C-0x2D: 1 (constant — likely format code: 1 = PCM)
      0x2E-0x2F: 0x3140=12608 (pcm) or 0x1140=4416 (koe) — NOT the sample
                  rate; standard PCM rates are 8000, 11025, 16000, 22050,
                  32000, 44100, 48000. Most likely a loop length in samples:
                  for pcm at 48 kHz stereo, 176462 / 12608 = 14.0 exact.
      0x34-0x37: data size (= file size - 56)
      0x38-...:  16-bit BIG-ENDIAN LPCM body (NO XOR — verified)
    """
    if data[:8] != b'BCLK0200':
        raise ValueError('bad magic')
    return {
        'hsize_p4': struct.unpack('>I', data[8:12])[0],
        'file_size_field': struct.unpack('>I', data[12:16])[0],
        'unknown_0x28': struct.unpack('>I', data[40:44])[0],
        'format': struct.unpack('>H', data[44:46])[0],
        'loop_samples_field': struct.unpack('>H', data[46:48])[0],
        'data_size': struct.unpack('>I', data[52:56])[0],
    }


def extract_pcm(src: Path, out_dir: Path, channels: int = 2, rate: int = 48000) -> int:
    """Extract a BCLK file as 16-bit PCM WAV.

    The BCLK body is plain 16-bit BIG-ENDIAN LPCM with a 56-byte Nitroplus
    header prepended. The BD-J JMF on every Blu-ray player handles this
    natively (LPCM is a mandatory BD-J codec). The byte order was the
    initial trap — reading BE as LE swaps every 16-bit word, producing
    static. The header's 0x2E field (12608/4416) is NOT a sample rate; the
    most likely interpretation is a loop length in samples (for pcm at
    48 kHz stereo, the data divides by 12608 exactly 14 times).

    Args:
        src: BCLK file path.
        out_dir: where to write the WAV.
        channels: 1 for voice (koe/), 2 for BGM (pcm/) — convention.
        rate: 48000 (BD-J mandatory LPCM rate).
    """
    import numpy as np
    with open(src, 'rb') as f:
        data = f.read()
    if data[:8] != b'BCLK0200':
        raise ValueError(f'{src.name}: bad magic, not a BCLK file')
    info = parse_bclk_header(data[:56])
    body = data[56:56 + info['data_size']]
    if len(body) < 2:
        print(f'  {src.name}: empty body, skipping')
        return 0

    # Reinterpret big-endian int16 as little-endian int16 (WAV is LE).
    # We can't just tobytes() the BE view; we need to byte-swap each 16-bit
    # word. numpy's view with dtype '<i2' does the swap for us.
    samples_be = np.frombuffer(body, dtype='>i2')
    samples_le = samples_be.astype('<i2', copy=False)

    out = out_dir / (src.stem + '.wav')
    frame_bytes = 2 * channels
    n_samples = (len(samples_le) // channels) * channels  # truncate to whole frame
    with wave.open(str(out), 'wb') as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples_le[:n_samples].tobytes())
    dur = n_samples / (rate * channels)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('auxdata', type=Path,
                    help='Path to AUXDATA directory (e.g. VABD-002-assets/AUXDATA)')
    ap.add_argument('--out', type=Path, default=Path('extracted'),
                    help='Output directory (default: ./extracted)')
    ap.add_argument('--channels', type=int, default=None,
                    help='Channels for PCM files. Default: 1 for koe/ (voice), 2 for pcm/ (BGM).')
    ap.add_argument('--rate', type=int, default=48000,
                    help='Sample rate for BCLK (default: 48000 Hz, the BD-J mandatory rate). '
                         'Override only if your ears tell you the default is wrong.')
    args = ap.parse_args()

    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)

    bdg_xor = load_xor_table(BDG_XOR_TABLE_PATH)
    fnt_xor = load_xor_table(FNT_XOR_TABLE_PATH)
    ttx_xor = load_xor_table(TTX_XOR_TABLE_PATH)

    bdg_dir = out / 'bdg'
    pcm_dir = out / 'pcm'
    koe_dir = out / 'koe'
    fnt_dir = out / 'fnt'
    ttx_dir = out / 'ttx'
    misc_dir = out / 'misc'
    for d in (bdg_dir, pcm_dir, koe_dir, fnt_dir, ttx_dir, misc_dir):
        d.mkdir(exist_ok=True)

    src = args.auxdata.resolve()
    if not src.is_dir():
        sys.exit(f'not a directory: {src}')

    n_bdg = n_pcm = n_koe = n_fnt = n_ttx = n_misc = 0
    failures = 0

    bdg_src = src / 'bdg'
    if bdg_src.is_dir():
        print(f'=== bdg/ ({len(list(bdg_src.iterdir()))} files) ===')
        for f in sorted(bdg_src.iterdir()):
            try:
                extract_bdg(f, bdg_dir, bdg_xor); n_bdg += 1
            except Exception as e:
                print(f'  FAIL: {f.name}: {e}', file=sys.stderr); failures += 1

    fnt_src = src / 'fnt'
    if fnt_src.is_dir():
        print(f'\n=== fnt/ ({len(list(fnt_src.iterdir()))} files) ===')
        for f in sorted(fnt_src.iterdir()):
            try:
                extract_fnt(f, fnt_dir, fnt_xor); n_fnt += 1
            except Exception as e:
                print(f'  FAIL: {f.name}: {e}', file=sys.stderr); failures += 1

    ttx_src = src / 'ttx'
    if ttx_src.is_dir():
        print(f'\n=== ttx/ ({len(list(ttx_src.iterdir()))} files) ===')
        for f in sorted(ttx_src.iterdir()):
            try:
                extract_ttx(f, ttx_dir, ttx_xor); n_ttx += 1
            except Exception as e:
                print(f'  FAIL: {f.name}: {e}', file=sys.stderr); failures += 1

    pcm_src = src / 'pcm'
    if pcm_src.is_dir():
        files = sorted(pcm_src.iterdir())
        print(f'\n=== pcm/ ({len(files)} files) ===')
        ch = args.channels if args.channels is not None else 2
        for f in files:
            try:
                extract_pcm(f, pcm_dir, ch, args.rate); n_pcm += 1
            except Exception as e:
                print(f'  FAIL: {f.name}: {e}', file=sys.stderr); failures += 1

    koe_src = src / 'koe'
    if koe_src.is_dir():
        files = sorted(koe_src.iterdir())
        print(f'\n=== koe/ ({len(files)} files) ===')
        ch = args.channels if args.channels is not None else 1
        for f in files:
            try:
                extract_pcm(f, koe_dir, ch, args.rate); n_koe += 1
            except Exception as e:
                print(f'  FAIL: {f.name}: {e}', file=sys.stderr); failures += 1

    print(f'\n=== Summary ===')
    print(f'  bdg:  {n_bdg} extracted')
    print(f'  fnt:  {n_fnt} extracted')
    print(f'  ttx:  {n_ttx} extracted')
    print(f'  pcm:  {n_pcm} extracted (16-bit BE LPCM, 48 kHz stereo)')
    print(f'  koe:  {n_koe} extracted (16-bit BE LPCM, 48 kHz mono)')
    if failures:
        print(f'  failures: {failures}', file=sys.stderr)


if __name__ == '__main__':
    main()
