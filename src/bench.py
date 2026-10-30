"""Digital twin of a low-cost firmness bench (texture-analyser style penetration test).

A stepper-driven linear axis pushes a spherical probe into a cream sample at constant speed while a
load cell (strain gauge + 24-bit HX711 ADC) records the force. The measured value is the force at a
fixed penetration depth (firmness, in newtons).

Every source of variation is modelled explicitly so that the measurement system can be qualified
(calibration, Gage R&R, drift monitoring) before any hardware is bought:

    sample      true firmness, rate dependence, local heterogeneity, temperature sensitivity
    load cell   gain error, non-linearity, hysteresis, creep, thermal zero drift
    ADC         white noise that depends on the sampling rate
    axis        step resolution, backlash, speed ripple
    operator    how the zero (probe/surface contact) is found: by eye, or automatically on a force threshold

All parameter values are explicit assumptions in the order of magnitude of hobby/lab components;
they are meant to be replaced by measured values once the real bench exists.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

G = 9.80665  # m/s²


@dataclass
class Sample:
    """A cream sample. `k` is its true firmness coefficient (N/mm^1.5) at 20 °C and 1 mm/s."""
    name: str
    k: float
    rate_exp: float = 0.12        # F grows with speed^rate_exp (shear-thinning paste)
    heterogeneity: float = 0.015  # relative std of k from one spot of the jar to another
    temp_coef: float = -0.02      # relative change of k per °C (creams soften when warm)
    edge_effect: float = 0.0      # relative increase of k at the rim of the jar (surface drying), ∝ r²

    def k_at(self, pos):
        """Spot firmness coefficient before micro-heterogeneity. `pos` = (x, y), normalised so that r = 1 at the rim."""
        if pos is None:
            return self.k
        return self.k * (1 + self.edge_effect * (pos[0] ** 2 + pos[1] ** 2))

    def force(self, depth_mm, speed_mm_s, temp_c, k_local):
        d = np.clip(depth_mm, 0, None)
        k = k_local * (1 + self.temp_coef * (temp_c - 20.0))
        return k * d ** 1.5 * (speed_mm_s / 1.0) ** self.rate_exp


@dataclass
class LoadCell:
    capacity_n: float = 5 * G         # 5 kg cell
    gain_error: float = 0.0           # relative, corrected by calibration
    nonlinearity: float = 0.0005      # of full scale, parabolic
    hysteresis: float = 0.0005        # of full scale, sign of motion
    creep: float = 0.0003             # of reading, over the hold
    zero_drift_n_per_c: float = 0.004  # thermal zero drift

    def read(self, f_true, moving_down, temp_c, rng):
        fs = self.capacity_n
        x = np.clip(f_true / fs, 0, 1)
        nl = self.nonlinearity * fs * 4 * x * (1 - x)
        hy = self.hysteresis * fs * (1 if moving_down else -1) * np.minimum(x * 4, 1)
        return f_true * (1 + self.gain_error) + nl + hy + self.zero_drift_n_per_c * (temp_c - 20.0)


@dataclass
class ADC:
    """HX711-type converter: 10 or 80 samples/s; the fast mode is noisier."""
    rate_sps: int = 80

    @property
    def noise_n(self):
        return 0.012 if self.rate_sps >= 80 else 0.005


@dataclass
class Axis:
    step_mm: float = 0.0025          # 1.8° stepper, 1/16 microstep, 2 mm lead screw (≈ 0.000625) rounded up for compliance
    backlash_mm: float = 0.03
    speed_ripple: float = 0.03       # relative std of speed
    lead_error: float = 0.0          # relative pitch error of the lead screw (systematic)


@dataclass
class Protocol:
    speed_mm_s: float = 1.0
    depth_mm: float = 5.0
    rate_sps: int = 80
    contact: str = "manual"          # "manual" = operator sets zero by eye; "auto" = force threshold
    threshold_n: float = 0.02
    averaging_window_mm: float = 0.2  # mean of the force over the last mm of travel around the target depth
    temp_control: bool = False       # sample conditioned at 20 ± 0.3 °C instead of room 20 ± 1.5 °C


@dataclass
class Operator:
    name: str
    zero_bias_mm: float = 0.0        # systematic: this person tends to stop early/late
    zero_sd_mm: float = 0.08         # random: by-eye contact detection


@dataclass
class Bench:
    cell: LoadCell = field(default_factory=LoadCell)
    axis: Axis = field(default_factory=Axis)
    cal_gain: float = 1.0            # set by calibrate()
    cal_offset: float = 0.0

    def measure(self, sample: Sample, protocol: Protocol, operator: Operator, rng, temp_c=None, return_curve=False,
                pos=None):
        """One test: returns the firmness value (N) and optionally the force/displacement curve.
        `pos` = (x, y) position of the probe in the jar (normalised, r = 1 at the rim); None = centre, no spatial effect."""
        if temp_c is None:
            temp_c = rng.normal(20.0, 0.3 if protocol.temp_control else 1.5)
        k_local = sample.k_at(pos) * (1 + rng.normal(0, sample.heterogeneity))
        adc = ADC(protocol.rate_sps)
        speed = protocol.speed_mm_s * (1 + rng.normal(0, self.axis.speed_ripple))
        # true penetration when the bench sets its zero (positive = probe already in the cream)
        if protocol.contact == "manual":
            # zero set by eye while jogging up and down: personal bias, random error and axis backlash
            zero_err = (operator.zero_bias_mm + rng.normal(0, operator.zero_sd_mm)
                        + rng.uniform(-self.axis.backlash_mm, self.axis.backlash_mm))
        else:
            # zero = first sample above the force threshold, always approached downwards (no backlash):
            # threshold depth + up to one sampling period of travel
            d_thr = (protocol.threshold_n / max(k_local, 1e-6)) ** (2 / 3)
            zero_err = d_thr + rng.uniform(0, speed / protocol.rate_sps)
        n = max(int(protocol.depth_mm / speed * protocol.rate_sps), 5)
        z_cmd = np.linspace(0, protocol.depth_mm, n)
        z_cmd = np.round(z_cmd / self.axis.step_mm) * self.axis.step_mm
        depth_true = z_cmd * (1 + self.axis.lead_error) + zero_err
        f_true = sample.force(depth_true, speed, temp_c, k_local)
        f_cell = self.cell.read(f_true, True, temp_c, rng)
        f_meas = (f_cell + rng.normal(0, adc.noise_n, n)) * self.cal_gain + self.cal_offset
        win = z_cmd >= protocol.depth_mm - protocol.averaging_window_mm
        value = float(np.mean(f_meas[win]))
        if return_curve:
            return value, z_cmd, f_meas
        return value

    # ------------------------------------------------------------------ calibration with dead weights
    def calibrate(self, masses_g=(0, 100, 200, 500, 1000, 2000, 3000), rng=None, temp_c=20.0, reps=20):
        """Load then unload known masses, fit F = a·reading + b by least squares, store the correction."""
        rng = rng or np.random.default_rng(0)
        rows = []
        self.cal_gain, self.cal_offset = 1.0, 0.0
        for direction, seq in (("charge", masses_g), ("décharge", masses_g[::-1])):
            for m in seq:
                f = m / 1000 * G
                r = self.cell.read(f, direction == "charge", temp_c, rng) + rng.normal(0, ADC(10).noise_n, reps)
                rows.append((direction, m, f, float(np.mean(r)), float(np.std(r, ddof=1))))
        ref = np.array([r[2] for r in rows])
        reading = np.array([r[3] for r in rows])
        a, b = np.polyfit(reading, ref, 1)
        self.cal_gain, self.cal_offset = float(a), float(b)
        corrected = a * reading + b
        err = corrected - ref
        up = {r[1]: c for r, c in zip(rows, corrected) if r[0] == "charge"}
        down = {r[1]: c for r, c in zip(rows, corrected) if r[0] == "décharge"}
        hyst = max(abs(up[m] - down[m]) for m in masses_g)
        fs = self.cell.capacity_n
        return {"points": rows, "gain": float(a), "offset_n": float(b),
                "linearity_err_pct_fs": float(100 * np.max(np.abs(err)) / fs),
                "hysteresis_pct_fs": float(100 * hyst / fs)}
