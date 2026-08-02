# BD-Alien-tools
Scripts to extract the assets from AsoBD BD-PG games

**Disclaimer**: These scripts were made with MiniMax-M3 because no one in their right mind would make RE scripts for obscure blu-ray VNs, hence, AI :thumbsup:

## Tested games

- AsoBD Doki Doki Trial (VABD-002)

## Supported file types

- **ttx**
- **bdg**
- **pcm**

## Extracting Assets

**NOTE**: You'll need the entire AUXDATA folder from your AsoBD BD-PG (obtainable from your local Yahoo Auctions Japan representative).

```bash
# 1. Extract everything: images, font, audio, decrypted TTX bytecode
python3 extract_assets.py <path-to-AUXDATA> --out <outpath>

# 2. (optional) Plain-text version of the dialogue (Shift-JIS extracted from the bytecode, sorted by scene, split into dialogue / narration)
python3 extract_text.py <outpath>/ttx --out <outpath>/text

# 3. Structured per-line data with audio + background + character expression links. Output is CSV + JSON by default (JSON recommended).
python extract_dialogue.py <ttx_dir> <koe_dir> <wav_dir> --no-csv --json
```

**NOTE**: Step 2 is only needed if you want to split the text between narration and dialog.

## What the scripts do (the techy stuff)

### `extract_assets.py`

Decrypts the Nitroplus proprietary formats and writes standard outputs:

- **BDG** (images): 76-byte header + body XOR'd with `FileIO.BDGAngoTbl`
  (4096-byte table extracted from the JAR's `FileIO.class` static
  initializer). The body is a PNG/JPEG/GIF.
- **FNT** (font): same scheme, smaller header + 512-byte
  `FNTAngoTbl`. Single 4096×4096 font atlas.
- **TTX** (script bytecode): 32-byte header + body XOR'd with
  `Script.TTXAngo` (512-byte table).
- **PCM/KOE** (audio): BCLK0200 wrapper + 16-bit **big-endian** PCM.
  NO XOR. The byte order was the trap — interpreting as little-endian
  produces static because every 16-bit word is byte-swapped. Extracted
  to 48 kHz WAVs (BD-J mandatory rate).
- **Misc** (`.dat` / `.tbl` files): passed through.

### `extract_text.py`

Scans each XOR-decrypted TTX file for Shift-JIS string sequences and
splits them into dialogue (`【name】「...」`) vs. narration vs.
internal labels. Outputs one `.txt` per scene and a combined
`ALL_TEXT.txt`.

### `extract_dialogue.py`

Parses the TTX bytecode (opcode `0x7F` is "display text"; its
operands include the line number that matches the koe filename) and
joins each line of dialogue to its audio, background, and character
expression. Outputs CSV and/or JSON.

Character expressions (the `cg` column) are recovered by scanning
the bytecode for length-prefixed strings matching `CG[A-Z]{2}\d+`
and propagating the most recent one to each text entry. All 125
unique CG references in the script output match real `cg*.bdg` files
on the disc.

## Format spec cheat-sheet

```
BCLK header (56 bytes, big-endian):
  0x00  8   "BCLK0200"
  0x08  4   0x38 (56, header size)
  0x0C  4   0 for pcm/; file size for koe/
  0x10 24   zeros
  0x28  4   0x0C (constant)
  0x2C  2   0x0001 (format code)
  0x2E  2   0x3140 (pcm) or 0x1140 (koe) — NOT sample rate;
             most likely loop length in samples
  0x34  4   data size
  0x38  N   16-bit big-endian LPCM body (NO XOR)

TTX "display text" opcode (0x7F):
  0x00  1   0x7F
  0x01  2   text length (LE)
  0x03  3   script line number (LE) — matches koe file line number
  0x06  3   read-already counter (LE)
  0x09  N   the Shift-JIS text

koe filename pattern: z{NNNN}{NNNNN}.pcm
  NNNN    = chapter (0001=Day18 main, 0002=Day19, 0003=Day20;
                   2718/2719/2720 = experience mode)
  NNNNN   = line number, matches the TTX script line
```

