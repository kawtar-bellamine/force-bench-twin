# Validation protocol: digital twin vs real bench

Goal: once the bench is built, confront every prediction of the digital twin with a real measurement, then either
validate the model or recalibrate it. Results go in `results/comparaison_simulation_reel.xlsx` (column "Banc réel");
the sheet computes the gap and the pass/fail against each acceptance criterion.

Estimated duration: 3 to 4 days of bench time, spread over two weeks for the drift part.

## 0. Before starting

- Bench assembled as in the README bill of materials, firmware flashed, `app/control_app.py` connected on the serial port.
- Room logged with a thermometer; samples conditioned at 20 °C for at least 2 h.
- Indexed 3D-printed jar holder with the 9 grid positions (x, y = −0.6, 0, +0.6 of the jar radius).
- Materials: class M1 masses (100 g to 3 kg), 10 creams covering the product range, a reference gel.

## 1. Calibration (half a day)

1. `TARE` with no load, then `CAL 1000` with the 1 kg mass.
2. Load then unload 0, 100, 200, 500, 1000, 2000, 3000 g; 20 readings per step.
3. Paste the readings in sheet `Etalonnage` of `qualification_banc.xlsx`.
4. Report linearity and hysteresis (% of full scale) in the comparison sheet.

## 2. Gauge alone on the reference gel (1 hour)

20 tests, optimised protocol (5 mm, 2 mm/s, 80 samples/s), each at a fresh spot. Report the relative standard
deviation. This is the bench's own scatter, used to separate it from the cream's heterogeneity.

## 3. Gage R&R (1 day)

10 creams × 3 operators × 3 trials, randomised order, run twice:

- protocol P0: zero set by eye, room temperature (to measure what the twin calls the initial protocol);
- protocol P1: automatic contact, samples at 20 °C.

Paste the measurements in `Mesures_P0_manuel` and `Mesures_P1_optimise`. Report %GRR and reproducibility.

## 4. Temperature sensitivity of the cream (half a day)

The twin *assumes* −2 %/°C. Measure it: 3 creams conditioned at 16, 20 and 24 °C, 5 tests each, slope of firmness
against temperature. Replace `temp_coef` in `src/bench.py` with the measured value. This also updates the
"Conditioning temperature" line of the uncertainty budget.

## 5. Firmness map (half a day)

5 creams × 9 positions × 2 tests (`Run` each position from the control app). Report the rim/centre pattern and the
local heterogeneity. Decide the routine sampling plan (centre, diagonal 3 spots or full grid) from the measured values.

## 6. Throughput (during step 3)

Time from `RUN` to `END` with the optimised protocol, including the approach.

## 7. Drift (two weeks, 5 min a day)

5 tests a day on the reference gel, pasted in `Suivi_reference`. No alarm is expected; the point is to set the
real control limits. Detection of a real drift can be checked by adding a small known mass offset on purpose.

## 8. Decision

- All criteria met: the twin is validated; the tuned protocol and the uncertainty budget apply as they are.
- A criterion fails: update the corresponding parameter in `src/bench.py` (gain, noise, backlash, heterogeneity,
  temperature sensitivity), re-run `python src/run_all.py`, and re-check the DOE conclusion before changing the protocol.
