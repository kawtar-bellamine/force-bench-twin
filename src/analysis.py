"""Measurement-system analysis for the firmness bench: Gage R&R (ANOVA method), protocol DOE, drift monitoring."""
from __future__ import annotations

from dataclasses import replace
import itertools

import numpy as np
import pandas as pd
from scipy import stats

from bench import Bench, Operator, Protocol, Sample

# 10 creams covering a product range (true firmness coefficient, N/mm^1.5)
SAMPLES = [Sample(f"S{i + 1:02d}", k) for i, k in enumerate(np.linspace(0.75, 1.35, 10))]
OPERATORS = [Operator("A", zero_bias_mm=0.05), Operator("B", zero_bias_mm=-0.04), Operator("C", zero_bias_mm=0.10)]


def run_study(bench: Bench, protocol: Protocol, rng, trials=3, samples=SAMPLES, operators=OPERATORS):
    rows = []
    for t in range(trials):
        for op in operators:
            for s in rng.permutation(len(samples)):      # randomised run order
                v = bench.measure(samples[s], protocol, op, rng)
                rows.append({"sample": samples[s].name, "operator": op.name, "trial": t + 1, "firmness_n": v})
    return pd.DataFrame(rows)


def gage_rr(df: pd.DataFrame, part="sample", op="operator", y="firmness_n"):
    """Crossed Gage R&R, two-way ANOVA with interaction (AIAG MSA method).

    Returns variance components, % of study variation and the number of distinct categories (ndc).
    """
    p, o = df[part].nunique(), df[op].nunique()
    r = len(df) // (p * o)
    grand = df[y].mean()
    mp = df.groupby(part)[y].mean()
    mo = df.groupby(op)[y].mean()
    mpo = df.groupby([part, op])[y].mean()
    ss_p = o * r * ((mp - grand) ** 2).sum()
    ss_o = p * r * ((mo - grand) ** 2).sum()
    ss_po = r * sum((mpo[(i, j)] - mp[i] - mo[j] + grand) ** 2 for i in mp.index for j in mo.index)
    ss_t = ((df[y] - grand) ** 2).sum()
    ss_e = ss_t - ss_p - ss_o - ss_po
    df_p, df_o, df_po, df_e = p - 1, o - 1, (p - 1) * (o - 1), p * o * (r - 1)
    ms_p, ms_o, ms_po, ms_e = ss_p / df_p, ss_o / df_o, ss_po / df_po, ss_e / df_e
    p_int = 1 - stats.f.cdf(ms_po / ms_e, df_po, df_e)
    if p_int > 0.25:  # interaction not significant: pool it into the error term (AIAG practice)
        ms_e = (ss_e + ss_po) / (df_e + df_po)
        ms_po_used, var_int = ms_e, 0.0
    else:
        ms_po_used, var_int = ms_po, max(0.0, (ms_po - ms_e) / r)
    var_rep = ms_e
    var_op = max(0.0, (ms_o - ms_po_used) / (p * r))
    var_part = max(0.0, (ms_p - ms_po_used) / (o * r))
    var_rr = var_rep + var_op + var_int
    var_tot = var_rr + var_part
    pct = lambda v: 100 * np.sqrt(v / var_tot)  # noqa: E731
    return {
        "repetabilite_pct": round(pct(var_rep), 1),
        "reproductibilite_pct": round(pct(var_op + var_int), 1),
        "grr_pct": round(pct(var_rr), 1),
        "piece_a_piece_pct": round(pct(var_part), 1),
        "ndc": int(np.floor(1.41 * np.sqrt(var_part / var_rr))) if var_rr > 0 else 99,
        "interaction_p_value": round(float(p_int), 3),
        "sd_grr_n": round(float(np.sqrt(var_rr)), 4),
        "anova": {"SS": [ss_p, ss_o, ss_po, ss_e], "df": [df_p, df_o, df_po, df_e]},
        "verdict": "acceptable" if pct(var_rr) < 10 else ("marginal" if pct(var_rr) < 30 else "inacceptable"),
    }


def doe(bench: Bench, rng, trials=3, reps=5):
    """Full factorial on the protocol: which settings make the measurement repeatable and reproducible?"""
    rows = []
    levels = {"contact": ["manual", "auto"], "temp_control": [False, True], "speed_mm_s": [0.5, 1.0, 2.0],
              "rate_sps": [10, 80], "averaging_window_mm": [0.02, 0.2]}
    for combo in itertools.product(*levels.values()):
        prm = dict(zip(levels, combo))
        prot = Protocol(**prm)
        res = [gage_rr(run_study(bench, prot, rng, trials)) for _ in range(reps)]   # averaged over replicate studies
        n_pts = max(int(prot.depth_mm / prot.speed_mm_s * prot.rate_sps), 5)
        rows.append({**prm, "grr_pct": round(float(np.mean([r["grr_pct"] for r in res])), 1),
                     "ndc": int(np.median([r["ndc"] for r in res])),
                     "duree_essai_s": round(prot.depth_mm / prot.speed_mm_s + 5, 1), "points": n_pts})
    d = pd.DataFrame(rows)
    effects = {}
    for f in levels:
        g = d.groupby(f)["grr_pct"].mean()
        effects[f] = {str(k): round(v, 1) for k, v in g.items()}
    return d, effects


def drift_monitoring(bench: Bench, protocol: Protocol, rng, days=60, per_day=5, drift_start=35, drift_per_day=0.0015,
                     lam=0.2, L=3.0, baseline_days=15):
    """Daily check on a reference gel. The load-cell gain starts drifting at `drift_start`; an EWMA chart
    built on the first `baseline_days` must raise the alarm before the bias becomes large."""
    ref = Sample("gel_reference", 1.0, heterogeneity=0.005)
    op = Operator("auto")
    rows = []
    gain0 = bench.cell.gain_error
    for day in range(1, days + 1):
        bench.cell.gain_error = gain0 + max(0, day - drift_start) * drift_per_day
        temp = rng.normal(20, 0.3 if protocol.temp_control else 1.5)
        vals = [bench.measure(ref, protocol, op, rng, temp_c=temp) for _ in range(per_day)]
        rows.append({"day": day, "mean_n": float(np.mean(vals)), "sd_n": float(np.std(vals, ddof=1)),
                     "temp_c": round(temp, 2), "true_gain_drift_pct": round(100 * (bench.cell.gain_error - gain0), 3)})
    bench.cell.gain_error = gain0
    d = pd.DataFrame(rows)
    base = d[d.day <= baseline_days]["mean_n"]
    mu, sd = base.mean(), base.std(ddof=1)
    z, ew, ucl, lcl, alarm = mu, [], [], [], None
    for i, x in enumerate(d["mean_n"], 1):
        z = lam * x + (1 - lam) * z
        w = L * sd * np.sqrt(lam / (2 - lam) * (1 - (1 - lam) ** (2 * i)))
        ew.append(z)
        ucl.append(mu + w)
        lcl.append(mu - w)
        if alarm is None and i > baseline_days and not (mu - w <= z <= mu + w):
            alarm = i
    d["ewma"], d["ucl"], d["lcl"] = ew, ucl, lcl
    bias_at_alarm = float(d.loc[d.day == alarm, "true_gain_drift_pct"].iloc[0]) if alarm else None
    return d, {"ligne_centrale_n": round(mu, 3), "debut_derive_jour": drift_start, "alarme_jour": alarm,
               "retard_jours": (alarm - drift_start) if alarm else None, "derive_au_moment_alarme_pct": bias_at_alarm}


# ---------------------------------------------------------------------------------------------- 3 × 3 firmness map
GRID = [(x, y) for y in (0.6, 0.0, -0.6) for x in (-0.6, 0.0, 0.6)]   # normalised positions, r = 1 at the jar rim
PLANS = {"centre, 1 essai": [(0.0, 0.0)],
         "centre, 3 essais": [(0.0, 0.0)] * 3,
         "diagonale, 3 points": [(-0.6, -0.6), (0.0, 0.0), (0.6, 0.6)],
         "grille complète, 9 points": GRID}


def mapping_study(bench: Bench, protocol: Protocol, rng, edge_effect=0.04, jars=(1, 3, 5, 7, 9), per_spot=2,
                  gel_tests=20):
    """Firmness map of several jars: separates the gauge, the micro-heterogeneity of the cream and the systematic
    pattern across the jar (here a firmer rim, e.g. surface drying), then compares sampling plans.

    Design: each jar is measured at 9 grid positions, `per_spot` neighbouring tests per position (each test is at a
    fresh spot, the test is quasi-destructive). The gauge alone is estimated on a homogeneous reference gel.
    Values are divided by the jar mean so that jars of different firmness can be pooled."""
    op = Operator("auto")
    rows = []
    for j in jars:
        s = replace(SAMPLES[j], edge_effect=edge_effect)
        for p_i, pos in enumerate(GRID):
            for t in range(per_spot):
                rows.append({"jar": s.name, "position": p_i + 1, "x": pos[0], "y": pos[1], "test": t + 1,
                             "firmness_n": bench.measure(s, protocol, op, rng, pos=pos)})
    d = pd.DataFrame(rows)
    d["relative"] = d["firmness_n"] / d.groupby("jar")["firmness_n"].transform("mean")
    # one-way ANOVA on positions (pooled over jars): between-position pattern vs within-position scatter
    g = d.groupby("position")["relative"]
    n_per_pos = int(g.size().iloc[0])
    ms_within = float(g.var(ddof=1).mean())
    ms_between = float(n_per_pos * g.mean().var(ddof=1))
    var_pattern = max(0.0, (ms_between - ms_within) / n_per_pos)
    p_pattern = float(1 - stats.f.cdf(ms_between / ms_within, len(GRID) - 1, len(GRID) * (n_per_pos - 1)))
    gel = Sample("gel_reference", 1.0, heterogeneity=0.005)
    gv = np.array([bench.measure(gel, protocol, op, rng) for _ in range(gel_tests)])
    var_gauge = float((gv.std(ddof=1) / gv.mean()) ** 2 - gel.heterogeneity ** 2)   # remove the gel's own scatter
    var_gauge = max(var_gauge, 0.0)
    var_micro = max(0.0, ms_within - var_gauge)
    pct = lambda v: round(100 * np.sqrt(v), 2)  # noqa: E731
    components = {"pattern_across_jar_pct": pct(var_pattern), "micro_heterogeneity_pct": pct(var_micro),
                  "gauge_pct": pct(var_gauge), "pattern_p_value": round(p_pattern, 4)}
    plans = sampling_plans(bench, protocol, rng, edge_effect)
    return d, components, plans


def sampling_plans(bench, protocol, rng, edge_effect, n_jars=300, sample_idx=4):
    """Error of each sampling plan on the true jar firmness (area average over the jar surface)."""
    op = Operator("auto")
    s = replace(SAMPLES[sample_idx], edge_effect=edge_effect)
    true = s.k * (1 + edge_effect * 0.5)            # mean of r² over a disc = 0.5
    out = []
    for name, spots in PLANS.items():
        errs = []
        for _ in range(n_jars):
            errs.append(np.mean([bench.measure(s, protocol, op, rng, pos=p) for p in spots]))
        errs = np.array(errs)
        out.append({"plan": name, "tests": len(spots), "values": errs})
    # true value in newtons: same protocol with k_true, averaged over many noise-limited runs at the centre
    s_true = replace(s, k=true, edge_effect=0.0, heterogeneity=0.0)
    f_true = float(np.mean([bench.measure(s_true, protocol, op, rng) for _ in range(400)]))
    res = []
    for o in out:
        e = 100 * (o["values"] / f_true - 1)
        res.append({"plan": o["plan"], "tests": o["tests"], "bias_pct": round(float(e.mean()), 2),
                    "sd_pct": round(float(e.std(ddof=1)), 2), "rms_error_pct": round(float(np.sqrt(np.mean(e ** 2))), 2),
                    "test_time_s": round(o["tests"] * (protocol.depth_mm / protocol.speed_mm_s + 5), 1)})
    return pd.DataFrame(res)
