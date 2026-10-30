"""Runs the full qualification of the firmness bench and writes results, figures, Excel workbook and Power BI tables."""
import json
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import analysis as A  # noqa: E402
from bench import Bench, Protocol  # noqa: E402
import uncertainty as U  # noqa: E402
from excel_report import comparison_workbook, write_workbook  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RES, PBI = ROOT / "results", ROOT / "powerbi"
INK, BLUE, RUST, GREEN, AMBER, GREY = "#1C1F22", "#2C7DA0", "#C4561D", "#3B8C5A", "#D99A1E", "#9AA3A8"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.edgecolor": "#4A5257", "axes.titleweight": "bold", "axes.titlesize": 11, "figure.dpi": 140})

MANUAL = Protocol()                                                       # starting protocol
OPTIM = Protocol(contact="auto", temp_control=True, rate_sps=80, speed_mm_s=2.0, averaging_window_mm=0.02)  # chosen after the DOE: best %GRR, fastest test


def main(seed=7):
    RES.mkdir(exist_ok=True)
    PBI.mkdir(exist_ok=True)
    rng = np.random.default_rng(seed)
    bench = Bench()
    cal = bench.calibrate(rng=rng)

    # 1. Gage R&R before / after
    df_man, df_opt = A.run_study(bench, MANUAL, rng), A.run_study(bench, OPTIM, rng)
    grr_man, grr_opt = A.gage_rr(df_man), A.gage_rr(df_opt)
    # robustness: same studies repeated 30 times
    mc = {k: [A.gage_rr(A.run_study(bench, p, rng))["grr_pct"] for _ in range(30)] for k, p in (("manuel", MANUAL), ("optimise", OPTIM))}

    # 2. protocol DOE
    doe_df, effects = A.doe(bench, rng)

    # 3. drift monitoring on a reference gel
    drift_df, drift = A.drift_monitoring(bench, OPTIM, rng)

    # 4. uncertainty budget (GUM) and its Monte Carlo check
    bud = U.budget(bench, OPTIM, rng, drift_rel_at_alarm=(drift["derive_au_moment_alarme_pct"] or 0.5) / 100)
    mcu = U.monte_carlo(bench, OPTIM, rng, bud)

    # 5. 3 × 3 firmness map and sampling plans
    map_df, map_comp, plans = A.mapping_study(bench, OPTIM, rng)

    summary = {
        "etalonnage": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in cal.items() if k != "points"},
        "gage_rr_protocole_initial": {k: v for k, v in grr_man.items() if k != "anova"},
        "gage_rr_protocole_optimise": {k: v for k, v in grr_opt.items() if k != "anova"},
        "gage_rr_30_etudes": {k: {"moyenne": round(float(np.mean(v)), 1), "p5": round(float(np.percentile(v, 5)), 1),
                                  "p95": round(float(np.percentile(v, 95)), 1)} for k, v in mc.items()},
        "plan_experiences_effets_grr_pct": effects,
        "meilleures_configurations": doe_df.sort_values("grr_pct").head(5).to_dict("records"),
        "suivi_derive": drift,
        "incertitude": {"mesure_n": round(bud["F_n"], 3), "u_c_n": round(bud["uc_n"], 4), "U_n_k2": round(bud["U_n"], 3),
                        "U_rel_pct": round(bud["U_rel_pct"], 2), "U_moyenne_3_points_n": round(bud["U_3spots_n"], 3),
                        "U_moyenne_3_points_rel_pct": round(bud["U_3spots_rel_pct"], 2),
                        "contributions_pct": {r["source"]: round(r["contribution_pct"], 1) for r in bud["rows"]},
                        "monte_carlo": {"ecart_type_n": round(mcu["sd_n"], 4), "intervalle_95_n": [round(v, 3) for v in mcu["ci95_n"]],
                                        "demi_largeur_95_n": round(mcu["half_width_95_n"], 3)}},
        "cartographie": {"composantes_variation": map_comp, "plans_echantillonnage": plans.to_dict("records")},
    }
    (RES / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False, default=str))
    doe_df.to_csv(RES / "doe.csv", index=False)

    # Power BI tables (star schema)
    fact = pd.concat([df_man.assign(protocol_id="P0_manuel"), df_opt.assign(protocol_id="P1_optimise")])
    fact.to_csv(PBI / "fact_mesures.csv", index=False)
    pd.DataFrame([{"protocol_id": "P0_manuel", **MANUAL.__dict__}, {"protocol_id": "P1_optimise", **OPTIM.__dict__}]).to_csv(
        PBI / "dim_protocoles.csv", index=False)
    pd.DataFrame([{"sample": s.name, "k_vrai": round(s.k, 3)} for s in A.SAMPLES]).to_csv(PBI / "dim_echantillons.csv", index=False)
    drift_df.to_csv(PBI / "fact_suivi_reference.csv", index=False)
    doe_df.to_csv(PBI / "fact_plan_experiences.csv", index=False)

    map_df.to_csv(PBI / "fact_cartographie.csv", index=False)
    pd.DataFrame(bud["rows"]).to_csv(PBI / "dim_budget_incertitude.csv", index=False)

    write_workbook(RES / "qualification_banc.xlsx", df_man, df_opt, cal, drift_df, doe_df, bud)
    comparison_workbook(RES / "comparaison_simulation_reel.xlsx", [
        ("Erreur de linéarité après étalonnage", "% PE", round(cal["linearity_err_pct_fs"], 3), "réel ≤ 0,2 % PE", "D{r}<=0.2"),
        ("Hystérésis après étalonnage", "% PE", round(cal["hysteresis_pct_fs"], 3), "réel ≤ 0,2 % PE", "D{r}<=0.2"),
        ("Écart-type du banc seul (gel de référence)", "%", map_comp["gauge_pct"], "écart ≤ 50 % de la valeur simulée",
         "ABS(E{r})<=0.5*C{r}"),
        ("% GRR, protocole initial", "%", grr_man["grr_pct"], "écart ≤ 5 points", "ABS(E{r})<=5"),
        ("% GRR, protocole optimisé", "%", grr_opt["grr_pct"], "réel < 10 %", "D{r}<10"),
        ("Reproductibilité, protocole optimisé", "%", grr_opt["reproductibilite_pct"], "réel < 3 %", "D{r}<3"),
        ("Sensibilité de la crème à la température", "%/°C", -2.0, "mesurée : remplace l'hypothèse si écart > 1", "ABS(E{r})<=1"),
        ("Hétérogénéité locale de la crème", "%", map_comp["micro_heterogeneity_pct"], "écart ≤ 1 point", "ABS(E{r})<=1"),
        ("Écart bord / centre (motif dans le pot)", "%", map_comp["pattern_across_jar_pct"], "écart ≤ 1 point", "ABS(E{r})<=1"),
        ("Durée d'un essai optimisé", "s", 7.5, "écart ≤ 1 s", "ABS(E{r})<=1"),
        ("Incertitude élargie relative (k = 2), 1 essai", "%", round(bud["U_rel_pct"], 2), "réel ≤ 5 %", "D{r}<=5"),
    ])
    figures(bench, cal, grr_man, grr_opt, mc, doe_df, drift_df, drift, rng)
    figures_new(bud, mcu, map_df, map_comp, plans)
    print(json.dumps(summary, indent=1, ensure_ascii=False, default=str))


def figures(bench, cal, grr_man, grr_opt, mc, doe_df, drift_df, drift, rng):
    s = A.SAMPLES[4]
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6), sharey=True)
    for ax, prot, col, title in ((axes[0], MANUAL, RUST, "Protocole initial (zéro réglé à l'œil)"),
                                 (axes[1], OPTIM, GREEN, "Protocole optimisé (zéro automatique, 20 °C)")):
        for op in A.OPERATORS:
            for _ in range(4):
                _, z, f = bench.measure(s, prot, op, rng, return_curve=True)
                ax.plot(z, f, color=col, alpha=0.35, lw=1)
        ax.axvspan(prot.depth_mm - prot.averaging_window_mm, prot.depth_mm, color=GREY, alpha=0.25)
        ax.set_title(title)
        ax.set_xlabel("Enfoncement commandé (mm)")
    axes[0].set_ylabel("Force (N)")
    fig.suptitle(f"Même crème ({s.name}), 3 opérateurs × 4 essais", fontsize=11, fontweight="bold")
    fig.tight_layout()
    fig.savefig(RES / "1_courbes_force_enfoncement.png")
    plt.close(fig)

    pts = pd.DataFrame(cal["points"], columns=["sens", "masse_g", "ref_n", "lecture_n", "sd_n"])
    corr = cal["gain"] * pts["lecture_n"] + cal["offset_n"]
    fig, ax = plt.subplots(figsize=(6, 3.4))
    for sens, col in (("charge", BLUE), ("décharge", AMBER)):
        m = pts["sens"] == sens
        ax.plot(pts.loc[m, "ref_n"], 1000 * (corr[m] - pts.loc[m, "ref_n"]), "o-", color=col, label=sens)
    ax.axhline(0, color=INK, lw=0.8)
    ax.set_xlabel("Force de référence, masses étalons (N)")
    ax.set_ylabel("Erreur après étalonnage (mN)")
    ax.set_title(f"Étalonnage : linéarité {cal['linearity_err_pct_fs']:.2f} % PE, hystérésis {cal['hysteresis_pct_fs']:.2f} % PE")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(RES / "2_etalonnage.png")
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6), gridspec_kw={"width_ratios": [1.1, 1]})
    ax = axes[0]
    names = ["Protocole initial", "Protocole optimisé"]
    rep = [grr_man["repetabilite_pct"], grr_opt["repetabilite_pct"]]
    rpr = [grr_man["reproductibilite_pct"], grr_opt["reproductibilite_pct"]]
    tot = [grr_man["grr_pct"], grr_opt["grr_pct"]]
    y = np.arange(2)
    ax.barh(y, tot, color=[RUST, GREEN], height=0.5)
    for i in range(2):
        ax.text(tot[i] + 0.6, y[i], f"GRR {tot[i]:.1f} %  (répét. {rep[i]:.1f}, reprod. {rpr[i]:.1f})", va="center", fontsize=9)
    ax.axvline(10, color=INK, ls="--", lw=1)
    ax.axvline(30, color=INK, ls=":", lw=1)
    ax.set_ylim(-0.75, 1.45)
    ax.text(10.4, -0.6, "< 10 % : acceptable", fontsize=8)
    ax.text(30.4, -0.6, "> 30 % : refusé", fontsize=8)
    ax.set_yticks(y, names)
    ax.set_xlim(0, 45)
    ax.set_xlabel("% de la variation de l'étude due au système de mesure")
    ax.set_title("Gage R&R (ANOVA, 10 crèmes × 3 opérateurs × 3 essais)")
    ax = axes[1]
    bp = ax.boxplot([mc["manuel"], mc["optimise"]], orientation="horizontal", widths=0.5, patch_artist=True, tick_labels=names)
    for patch, col in zip(bp["boxes"], (RUST, GREEN)):
        patch.set_facecolor(col)
        patch.set_alpha(0.5)
    ax.axvline(10, color=INK, ls="--", lw=1)
    ax.set_xlabel("% GRR sur 30 études répétées")
    ax.set_title("Robustesse du résultat")
    fig.tight_layout()
    fig.savefig(RES / "3_gage_rr.png")
    plt.close(fig)

    labels = {"contact": "Détection du contact", "temp_control": "Échantillon à 20 °C", "speed_mm_s": "Vitesse (mm/s)",
              "rate_sps": "Échantillonnage (pts/s)", "averaging_window_mm": "Fenêtre de moyenne (mm)"}
    fig, axes = plt.subplots(1, 5, figsize=(12, 3), sharey=True)
    for ax, (f, lab) in zip(axes, labels.items()):
        g = doe_df.groupby(f)["grr_pct"].mean()
        ax.plot([str(v) for v in g.index], g.values, "o-", color=BLUE, lw=2)
        ax.set_title(lab, fontsize=9)
    axes[0].set_ylabel("% GRR moyen")
    fig.suptitle("Plan d'expériences sur le protocole : effet moyen de chaque réglage (48 configurations × 5 études)",
                 fontsize=10, fontweight="bold")
    fig.tight_layout()
    fig.savefig(RES / "4_plan_experiences.png")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 3.4))
    ax.plot(drift_df.day, drift_df.mean_n, "o", color=GREY, ms=3, label="moyenne du jour (5 mesures)")
    ax.plot(drift_df.day, drift_df.ewma, color=BLUE, lw=2, label="EWMA (λ = 0,2)")
    ax.plot(drift_df.day, drift_df.ucl, color=RUST, ls="--", lw=1, label="limites de contrôle")
    ax.plot(drift_df.day, drift_df.lcl, color=RUST, ls="--", lw=1)
    ax.axvline(drift["debut_derive_jour"], color=AMBER, lw=1.5)
    ax.text(drift["debut_derive_jour"] + 0.5, ax.get_ylim()[1], "début de dérive du capteur", color=AMBER_T(), fontsize=8, va="top")
    if drift["alarme_jour"]:
        ax.axvline(drift["alarme_jour"], color=RUST, lw=1.5)
        ax.text(drift["alarme_jour"] + 0.5, ax.get_ylim()[0], f"alarme jour {drift['alarme_jour']}", color=RUST, fontsize=8)
    ax.set_xlabel("Jour")
    ax.set_ylabel("Fermeté du gel de référence (N)")
    ax.set_title("Suivi quotidien d'un gel de référence : la dérive du capteur est détectée avant de fausser les mesures")
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    fig.tight_layout()
    fig.savefig(RES / "5_suivi_derive.png")
    plt.close(fig)


def figures_new(bud, mcu, map_df, map_comp, plans):
    # 6. firmness map
    fig, axes = plt.subplots(1, 3, figsize=(12.5, 3.8), gridspec_kw={"width_ratios": [1.1, 1, 1.35]})
    ax = axes[0]
    m = map_df.groupby(["y", "x"])["relative"].mean().unstack().sort_index(ascending=False)
    im = ax.imshow(100 * (m.values - 1), cmap="RdBu_r", vmin=-2.5, vmax=2.5, extent=(-0.9, 0.9, -0.9, 0.9))
    ax.add_patch(plt.Circle((0, 0), 1.0, fill=False, color=INK, lw=1.5))
    for (yy, xx), v in np.ndenumerate(m.values):
        ax.text(m.columns[xx], m.index[yy], f"{100 * (v - 1):+.1f} %", ha="center", va="center", fontsize=9)
    ax.set_xlim(-1.1, 1.1)
    ax.set_ylim(-1.1, 1.1)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_title("Carte 3 × 3 : écart au pot (%)", fontsize=10)
    for sp in ax.spines.values():
        sp.set_visible(False)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    ax = axes[1]
    labs = ["Motif\n(bord plus ferme)", "Hétérogénéité\nlocale", "Banc\nseul"]
    vals = [map_comp["pattern_across_jar_pct"], map_comp["micro_heterogeneity_pct"], map_comp["gauge_pct"]]
    ax.bar(labs, vals, color=[AMBER, RUST, BLUE], width=0.6)
    for i, v in enumerate(vals):
        ax.text(i, v + 0.03, f"{v:.2f} %", ha="center", fontsize=9)
    ax.set_ylabel("Écart-type relatif (%)")
    ax.set_title("Qui fait varier la mesure ?")
    ax.tick_params(axis="x", labelsize=8)
    ax = axes[2]
    y = np.arange(len(plans))[::-1]
    ax.barh(y, plans["rms_error_pct"], color=[RUST, AMBER, GREEN, BLUE], height=0.55)
    for yi, (_, r) in zip(y, plans.iterrows()):
        ax.text(r["rms_error_pct"] + 0.05, yi, f"biais {r['bias_pct']:+.1f} %, dispersion {r['sd_pct']:.1f} %, {r['test_time_s']:.0f} s",
                va="center", fontsize=8)
    ax.set_yticks(y, plans["plan"])
    ax.set_xlim(0, max(plans["rms_error_pct"]) * 2.4)
    ax.set_xlabel("Erreur quadratique sur la fermeté du pot (%)")
    ax.set_title("Plan d'échantillonnage du pot")
    fig.suptitle("Cartographie de fermeté : 5 pots × 9 positions × 2 essais, plus 20 essais sur gel de référence",
                 fontsize=11, fontweight="bold")
    fig.tight_layout()
    fig.savefig(RES / "6_cartographie.png")
    plt.close(fig)

    # 7. uncertainty budget
    fig, axes = plt.subplots(1, 2, figsize=(12, 3.8), gridspec_kw={"width_ratios": [1.3, 1]})
    ax = axes[0]
    rows = sorted(bud["rows"], key=lambda r: r["contribution_pct"])
    ax.barh([r["source"] for r in rows], [r["contribution_pct"] for r in rows],
            color=[BLUE if r["type"] == "A" else AMBER for r in rows], height=0.6)
    for i, r in enumerate(rows):
        ax.text(r["contribution_pct"] + 1, i, f"{r['contribution_pct']:.1f} %  (u = {1000 * r['u_n']:.1f} mN)", va="center", fontsize=8)
    ax.set_xlim(0, 125)
    ax.tick_params(axis="y", labelsize=8)
    ax.set_xlabel("Part de la variance composée (%)  ·  bleu : type A, ambre : type B")
    ax.set_title(f"Budget GUM : F = {bud['F_n']:.2f} N ± {bud['U_n']:.2f} N (k = 2, {bud['U_rel_pct']:.1f} %)")
    ax = axes[1]
    ax.hist(mcu["values"], bins=50, color=GREY, alpha=0.8)
    lo, hi = mcu["ci95_n"]
    ax.axvspan(lo, hi, color=GREEN, alpha=0.15, label=f"Monte Carlo 95 % : ± {mcu['half_width_95_n']:.2f} N")
    ax.axvline(bud["F_n"] - bud["U_n"], color=RUST, ls="--", lw=1.2, label=f"GUM : ± {bud['U_n']:.2f} N")
    ax.axvline(bud["F_n"] + bud["U_n"], color=RUST, ls="--", lw=1.2)
    ax.set_xlabel("Fermeté simulée (N), 4 000 tirages de tous les effets")
    ax.set_title("Vérification par Monte Carlo (GUM S1)")
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    fig.tight_layout()
    fig.savefig(RES / "7_budget_incertitude.png")
    plt.close(fig)


def AMBER_T():
    return "#B7791F"


if __name__ == "__main__":
    main()
