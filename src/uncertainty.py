"""Uncertainty budget of one firmness measurement (GUM, JCGM 100) and its Monte Carlo check (GUM Supplement 1, JCGM 101).

Measurand: firmness F of one spot of a cream jar, optimised protocol (automatic contact, sample at 20 °C).

Type A components come from the bench studies themselves (repeated tests, Gage R&R). Type B components are the
systematic effects that a repeatability study cannot see because they shift every measurement the same way
(reference masses, calibration residual, conditioning temperature, sensor drift between two daily checks, lead-screw
pitch, display resolution). Their half-widths are explicit assumptions, listed in `TYPE_B`, to be replaced by
certificate and datasheet values once the real components are bought.
"""
from __future__ import annotations

import copy

import numpy as np

from analysis import OPERATORS, SAMPLES, gage_rr, run_study
from bench import Bench, Operator, Protocol

SQ3 = np.sqrt(3)

# half-widths of the rectangular distributions (assumptions, see module docstring)
TYPE_B = {
    "mass_rel": 5e-5,          # OIML R111 class M1 masses: 50 mg on 1 kg
    "temp_setpoint_c": 0.5,    # accuracy of the conditioning cabinet around 20 °C
    "drift_rel": None,         # filled with the gain drift reached when the EWMA chart raises its alarm
    "lead_rel": 1e-3,          # lead-screw pitch error, ordinary trapezoidal screw
    "resolution_n": 0.0005,    # half of the 0.001 N displayed digit
}


def budget(bench: Bench, protocol: Protocol, rng, drift_rel_at_alarm: float, sample=None, n_rep=50, k=2.0):
    """Returns the budget as a list of rows plus the combined and expanded uncertainty."""
    sample = sample or SAMPLES[4]
    tb = dict(TYPE_B, drift_rel=drift_rel_at_alarm)
    op = Operator("auto")
    vals = np.array([bench.measure(sample, protocol, op, rng) for _ in range(n_rep)])
    F = float(vals.mean())
    s_rep = float(vals.std(ddof=1))
    # operator variance pooled over 10 Gage R&R studies: one study with 3 operators gives a very noisy estimate
    var_op = []
    for _ in range(10):
        g = gage_rr(run_study(bench, protocol, rng))
        var_op.append((g["sd_grr_n"] * g["reproductibilite_pct"] / g["grr_pct"]) ** 2 if g["grr_pct"] else 0.0)
    s_repro = float(np.sqrt(np.mean(var_op)))
    cal = bench.calibrate(rng=np.random.default_rng(int(rng.integers(1e9))))
    resid_n = cal["linearity_err_pct_fs"] / 100 * bench.cell.capacity_n
    temp_coef = abs(sample.temp_coef)
    rows = [
        # name, type, distribution, half-width or sd, unit, divisor, sensitivity coefficient (N per unit), note
        ("Répétabilité (un essai)", "A", "normal", s_rep, "N", 1.0, 1.0,
         f"écart-type de {n_rep} essais sur {sample.name} : zéro de la sonde, vitesse, bruit du convertisseur, ±0,3 °C, hétérogénéité locale"),
        ("Reproductibilité (opérateurs)", "A", "normal", s_repro, "N", 1.0, 1.0, "Gage R&R du protocole optimisé, moyenne de 10 études"),
        ("Masses étalons (classe M1)", "B", "rectangular", tb["mass_rel"], "relative", SQ3, F, "certificat d'étalonnage"),
        ("Résidu d'étalonnage (linéarité, hystérésis)", "B", "rectangular", resid_n, "N", SQ3, 1.0,
         "erreur maximale après l'ajustement aux masses"),
        ("Température de conditionnement", "B", "rectangular", tb["temp_setpoint_c"], "°C", SQ3, temp_coef * F,
         "justesse de l'enceinte × sensibilité thermique de la crème (à mesurer)"),
        ("Dérive thermique du zéro du capteur", "B", "rectangular", tb["temp_setpoint_c"], "°C", SQ3,
         bench.cell.zero_drift_n_per_c, "ordre de grandeur de fiche technique"),
        ("Dérive du capteur entre deux contrôles", "B", "rectangular", tb["drift_rel"], "relative", SQ3, F,
         "plus grande dérive de gain avant l'alarme EWMA"),
        ("Pas de la vis", "B", "rectangular", tb["lead_rel"], "relative", SQ3, 1.5 * F,
         "F ∝ profondeur^1,5, donc dF/F = 1,5 × dz/z"),
        ("Résolution d'affichage", "B", "rectangular", tb["resolution_n"], "N", SQ3, 1.0, "chiffre de 0,001 N"),
    ]
    out = []
    for name, typ, dist, x, unit, div, c, note in rows:
        u = abs(c) * x / div
        out.append({"source": name, "type": typ, "distribution": dist, "value": x, "unit": unit, "divisor": div,
                    "sensitivity": c, "u_n": u, "note": note})
    uc = float(np.sqrt(sum(r["u_n"] ** 2 for r in out)))
    for r in out:
        r["contribution_pct"] = 100 * r["u_n"] ** 2 / uc ** 2
    # result reported as the mean of 3 spots (diagonal plan): only the repeatability term is divided by sqrt(3)
    uc3 = float(np.sqrt(uc ** 2 - out[0]["u_n"] ** 2 + out[0]["u_n"] ** 2 / 3))
    return {"F_n": F, "rows": out, "uc_n": uc, "k": k, "U_n": k * uc, "U_rel_pct": 100 * k * uc / F,
            "U_3spots_n": k * uc3, "U_3spots_rel_pct": 100 * k * uc3 / F, "sample": sample.name, "type_b": tb}


def monte_carlo(bench: Bench, protocol: Protocol, rng, bud: dict, sample=None, n=4000):
    """GUM Supplement 1: draw every systematic effect, run the full bench model, compare with the GUM result."""
    sample = sample or SAMPLES[4]
    tb = bud["type_b"]
    resid = next(r["value"] for r in bud["rows"] if r["source"].startswith("Résidu"))
    vals = np.empty(n)
    b = copy.deepcopy(bench)
    gain0, lead0 = b.cal_gain, b.axis.lead_error
    for i in range(n):
        b.cal_gain = gain0 * (1 + rng.uniform(-tb["mass_rel"], tb["mass_rel"]) + rng.uniform(-tb["drift_rel"], tb["drift_rel"]))
        b.axis.lead_error = lead0 + rng.uniform(-tb["lead_rel"], tb["lead_rel"])
        temp = 20 + rng.uniform(-tb["temp_setpoint_c"], tb["temp_setpoint_c"]) + rng.normal(0, 0.3)
        op = OPERATORS[rng.integers(len(OPERATORS))]
        v = b.measure(sample, protocol, op, rng, temp_c=temp) + rng.uniform(-resid, resid)
        vals[i] = np.round(v, 3)
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return {"values": vals, "sd_n": float(vals.std(ddof=1)), "mean_n": float(vals.mean()),
            "ci95_n": (float(lo), float(hi)), "half_width_95_n": float((hi - lo) / 2)}
