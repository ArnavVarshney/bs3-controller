"""Fan-curve daemon logic: temp -> target RPM.

- Linear interpolation between (temp_c, rpm) points.
- Below first point: 0 (passive stop). Step 0->500 across 1 degree
  because 1..499 is a stall band (tach flips 0-400).
- 100 RPM deadband: don't rewrite the cooler for tiny changes
  (firmware ramps itself at ~60 RPM/s anyway).
- 2C down-hysteresis: step up fast, step down only after genuinely
  cooler (breaks the chase loop with the laptop's own EC fans).
- Exponential smoothing (fast up, slow down) + 90C panic bypass.
- Supply ceiling applied last (2700 on laptop USB, 3300 mid, 4000 full).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import protocol as P

DEFAULT_CURVE = [
    (35.0, 1000),
    (45.0, 1400),
    (55.0, 2000),
    (65.0, 2700),
    (75.0, 3400),
    (85.0, 4000),
]
PANIC_C = 90.0
DEADBAND_RPM = 100
HYST_C = 2.0  # down-only temp hysteresis: a dip smaller than this holds RPM


@dataclass
class Curve:
    points: list[tuple[float, int]] = field(default_factory=lambda: list(DEFAULT_CURVE))
    panic_c: float = PANIC_C
    deadband: int = DEADBAND_RPM
    hyst_c: float = HYST_C
    _smooth: float | None = None
    _last_sent: int | None = None
    _anchor: float | None = None  # smoothed temp at the current level

    def target_for(self, temp_c: float) -> int:
        pts = sorted(self.points)
        if temp_c < pts[0][0]:
            return 0
        if temp_c >= pts[-1][0]:
            return pts[-1][1]
        for (t0, r0), (t1, r1) in zip(pts, pts[1:]):
            if t0 <= temp_c < t1:
                frac = (temp_c - t0) / (t1 - t0)
                return round(r0 + frac * (r1 - r0))
        return pts[-1][1]

    def update(self, raw_c: float, supply: int = 3,
               model: str | None = None,
               alpha_up: float = 0.5, alpha_down: float = 0.15) -> tuple[int, bool]:
        """Feed a raw reading. Returns (rpm_to_send, changed).

        changed=False means within deadband or inside down-hysteresis:
        skip the write. Stepping up is immediate (heat is urgent);
        stepping down needs the smoothed temp to fall hyst_c below the
        level that set the current RPM — this breaks the pad/chassis-fan
        chase loop where each side reacts to the other's cooling.
        Panic (>=panic_c) bypasses smoothing, hysteresis and deadband.
        """
        if self._smooth is None:
            self._smooth = raw_c
        else:
            a = alpha_up if raw_c > self._smooth else alpha_down
            self._smooth += a * (raw_c - self._smooth)
        temp = raw_c if raw_c >= self.panic_c else self._smooth
        want = P.clamp_rpm(self.target_for(temp), supply, model)
        if raw_c >= self.panic_c:
            want = P.clamp_rpm(4000, supply, model)
        if self._last_sent is None or raw_c >= self.panic_c:
            self._last_sent = want
            self._anchor = temp
            return want, True
        if (want == 0) != (self._last_sent == 0):
            self._last_sent = want
            self._anchor = temp
            return want, True
        if want > self._last_sent:
            if want - self._last_sent >= self.deadband:
                self._last_sent = want
                self._anchor = temp
                return want, True
            return self._last_sent, False
        if want < self._last_sent:
            if self._last_sent - want >= self.deadband \
                    and temp <= (self._anchor if self._anchor is not None else temp) - self.hyst_c:
                self._last_sent = want
                self._anchor = temp
                return want, True
            return self._last_sent, False
        return self._last_sent, False
