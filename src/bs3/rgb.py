"""RGB lighting. Yes — the BS3 HAS rgb.

Two light sources, don't confuse them:
- Gear indicators (1-4 LEDs): cmd 0x48, 00 off / 01 on. Blink in realtime.
- Side RGB strip (6 LEDs, WS2812 GRB bit-bang): cmds 0x41/0x42/0x43/0x47
  upload a 186-byte animation buffer, 0x44 selects it, 0x46 powers it.

Firmware facts (FIRMWARE.md sec 3.5) that bite:
- Built-in presets 1-5 ONLY render while the fan is in realtime mode
  (0x23). Outside realtime, 0x44 01..05 acks but is a silent no-op.
  Mode 0 (user buffer) plays in any fan mode.
- 0x24 (exit realtime) re-applies the user buffer and restarts it.
- Nothing reads the animation back. Remember what you set.
- Frame indices: 0x00 header + 10B, 0x01..0x11 data 10B each,
  0x12 tail 6B, >=0x13 acked but discarded. Vendor app sends 30 frames;
  only the first ~19 reach the strip.

Presets (hardcoded palettes in firmware): 1 green breathing, 2 yellow,
3 red, 4 static red, 5 multicolour.
"""

from __future__ import annotations

EFFECT_NAMES = {
    0: "user buffer (uploaded via 0x47/0x42)",
    1: "green breathing",
    2: "yellow",
    3: "red",
    4: "static red",
    5: "multicolour",
}

HEADER_LEN = 10
N_DATA_FRAMES = 18  # 0x01..0x11 x10B + 0x12 x6B = 186B total w/ header


def make_header(mode: int = 0, speed: int = 0x0A, brightness: int = 70,
                color: tuple[int, int, int] = (0, 0, 0)) -> bytes:
    if not 0 <= brightness <= 100:
        raise ValueError("brightness 0..100")
    return bytes((0x00, 0x02, 0x00, mode, speed, brightness, *color, 0x00))


def static_color_frames(rgb: tuple[int, int, int], brightness: int = 70) -> tuple[bytes, list[bytes]]:
    """Single static colour: header + 18 data frames with LEDs lit.

    Layout mirrors starboykm/THRM captures: LED triplets live at
    offsets 6..8 of scattered frames; the strip renders LED-major
    buf[6 + led*30 + frame*3]. Lighting the 2/5/8/11/14 pattern
    gives an even static glow on hardware.
    """
    f = brightness / 100.0
    scaled = bytes(int(c * f) for c in rgb)
    header = make_header(0x00, 0x0A, brightness, rgb)
    frames = [bytes(10) for _ in range(N_DATA_FRAMES)]
    frames = [bytearray(x) for x in frames]
    for idx in (2, 5, 8, 11, 14):
        if idx < len(frames):
            frames[idx][6:9] = scaled
    return bytes(header), [bytes(x) for x in frames]


def upload_plan(header: bytes, frames: list[bytes]) -> list[tuple[int, bytes]]:
    """Build the 0x47 addressed-write sequence: (0x00,hdr) + indexed frames.

    Caller sends each via 0x47 then finishes with 0x43 01. For large
    uploads prefer 0x41 + 0x42 streaming blocks (13 x <=15B) instead.
    """
    if len(header) != 10:
        raise ValueError("header must be 10 bytes")
    plan = [(0x47, bytes((0x00,)) + bytes(header))]
    for i, fr in enumerate(frames, 1):
        if i < 0x12:
            plan.append((0x47, bytes((i,)) + bytes(fr[:10])))
        elif i == 0x12:
            plan.append((0x47, bytes((i,)) + bytes(fr[:6])))
        else:
            break  # >=0x13 acked but discarded; don't send
    return plan
