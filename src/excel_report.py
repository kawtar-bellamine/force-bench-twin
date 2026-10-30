"""Excel workbook with live formulas: the Gage R&R ANOVA, the calibration fit and the EWMA chart are recomputed by
Excel itself from the raw measurements, so the results can be audited (and reused on real bench data)."""
from openpyxl import Workbook
from openpyxl.chart import LineChart, Reference
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HEAD = PatternFill("solid", fgColor="1C1F22")
KEY = PatternFill("solid", fgColor="DCEBF2")
WHITE = Font(color="FFFFFF", bold=True)
BOLD = Font(bold=True)


def _table(ws, df, r0=1, c0=1):
    for j, col in enumerate(df.columns):
        c = ws.cell(r0, c0 + j, col)
        c.fill, c.font = HEAD, WHITE
    for i, row in enumerate(df.itertuples(index=False), 1):
        for j, v in enumerate(row):
            ws.cell(r0 + i, c0 + j, v if not hasattr(v, "item") else v.item())
    for j in range(len(df.columns)):
        ws.column_dimensions[get_column_letter(c0 + j)].width = 16


def _grr_sheet(wb, name, df):
    raw = wb.create_sheet(f"Mesures_{name}")
    _table(raw, df[["sample", "operator", "trial", "firmness_n"]])
    n = len(df) + 1
    S, O, V = f"Mesures_{name}!$A$2:$A${n}", f"Mesures_{name}!$B$2:$B${n}", f"Mesures_{name}!$D$2:$D${n}"
    parts, ops = sorted(df["sample"].unique()), sorted(df["operator"].unique())
    p, o = len(parts), len(ops)
    ws = wb.create_sheet(f"GRR_{name}")
    ws["A1"] = f"Gage R&R, méthode ANOVA (AIAG MSA), protocole {name} : toutes les cellules sont des formules"
    ws["A1"].font = Font(bold=True, size=12)
    # matrix of cell means part x operator
    ws["A3"] = "Moyennes"
    ws["A3"].font = BOLD
    for j, op in enumerate(ops):
        ws.cell(3, 2 + j, op).font = BOLD
    ws.cell(3, 2 + o, "Moyenne pièce").font = BOLD
    for i, pt in enumerate(parts):
        r = 4 + i
        ws.cell(r, 1, pt)
        for j, op in enumerate(ops):
            ws.cell(r, 2 + j, f'=AVERAGEIFS({V},{S},$A{r},{O},{get_column_letter(2 + j)}$3)')
        ws.cell(r, 2 + o, f"=AVERAGEIF({S},$A{r},{V})")
    rl = 4 + p
    ws.cell(rl, 1, "Moyenne opérateur").font = BOLD
    for j in range(o):
        ws.cell(rl, 2 + j, f"=AVERAGEIF({O},{get_column_letter(2 + j)}$3,{V})")
    ws.cell(rl, 2 + o, f"=AVERAGE({V})")
    M = f"$B$4:${get_column_letter(1 + o)}${3 + p}"
    PM = f"${get_column_letter(2 + o)}$4:${get_column_letter(2 + o)}${3 + p}"
    OM = f"$B${rl}:${get_column_letter(1 + o)}${rl}"
    G = f"${get_column_letter(2 + o)}${rl}"
    k = rl + 2
    lines = [
        ("p (pièces)", p), ("o (opérateurs)", o), ("r (essais)", f"=COUNT({V})/(B{k}*B{k + 1})"),
        ("SS pièces", f"=B{k + 1}*B{k + 2}*SUMPRODUCT(({PM}-{G})^2)"),
        ("SS opérateurs", f"=B{k}*B{k + 2}*SUMPRODUCT(({OM}-{G})^2)"),
        ("SS interaction", f"=B{k + 2}*SUMPRODUCT(({M}-{PM}-{OM}+{G})^2)"),
        ("SS total", f"=DEVSQ({V})"),
        ("SS erreur", f"=B{k + 6}-B{k + 3}-B{k + 4}-B{k + 5}"),
        ("MS pièces", f"=B{k + 3}/(B{k}-1)"), ("MS opérateurs", f"=B{k + 4}/(B{k + 1}-1)"),
        ("MS interaction", f"=B{k + 5}/((B{k}-1)*(B{k + 1}-1))"),
        ("MS erreur", f"=B{k + 7}/(B{k}*B{k + 1}*(B{k + 2}-1))"),
        ("p-valeur interaction", f"=_xlfn.F.DIST.RT(B{k + 10}/B{k + 11},(B{k}-1)*(B{k + 1}-1),B{k}*B{k + 1}*(B{k + 2}-1))"),
        ("MS erreur retenue (interaction poolée si p > 0,25)",
         f"=IF(B{k + 12}>0.25,(B{k + 7}+B{k + 5})/(B{k}*B{k + 1}*(B{k + 2}-1)+(B{k}-1)*(B{k + 1}-1)),B{k + 11})"),
        ("MS interaction retenue", f"=IF(B{k + 12}>0.25,B{k + 13},B{k + 10})"),
        ("Var. répétabilité", f"=B{k + 13}"),
        ("Var. interaction", f"=IF(B{k + 12}>0.25,0,MAX(0,(B{k + 10}-B{k + 11})/B{k + 2}))"),
        ("Var. opérateur", f"=MAX(0,(B{k + 9}-B{k + 14})/(B{k}*B{k + 2}))"),
        ("Var. pièce", f"=MAX(0,(B{k + 8}-B{k + 14})/(B{k + 1}*B{k + 2}))"),
        ("Var. GRR", f"=B{k + 15}+B{k + 16}+B{k + 17}"),
        ("% répétabilité", f"=100*SQRT(B{k + 15}/(B{k + 19}+B{k + 18}))"),
        ("% reproductibilité", f"=100*SQRT((B{k + 16}+B{k + 17})/(B{k + 19}+B{k + 18}))"),
        ("% GRR", f"=100*SQRT(B{k + 19}/(B{k + 19}+B{k + 18}))"),
        ("ndc", f"=INT(1.41*SQRT(B{k + 18}/B{k + 19}))"),
        ("Verdict", f'=IF(B{k + 22}<10,"acceptable",IF(B{k + 22}<30,"marginal","inacceptable"))'),
    ]
    for i, (lab, f) in enumerate(lines):
        ws.cell(k + i, 1, lab)
        c = ws.cell(k + i, 2, f)
        c.number_format = "0.000"
        if lab in ("% GRR", "ndc", "Verdict", "% répétabilité", "% reproductibilité"):
            ws.cell(k + i, 1).font = BOLD
            c.font, c.fill = BOLD, KEY
    ws.conditional_formatting.add(f"B{k + 22}", CellIsRule(operator="lessThan", formula=["10"], fill=PatternFill("solid", fgColor="E1EFE5")))
    ws.conditional_formatting.add(f"B{k + 22}", CellIsRule(operator="greaterThanOrEqual", formula=["10"], fill=PatternFill("solid", fgColor="F7E3D8")))
    ws.column_dimensions["A"].width = 46
    for j in range(2, 3 + o):
        ws.column_dimensions[get_column_letter(j)].width = 14
    return f"GRR_{name}!B{k + 22}"


def write_workbook(path, df_man, df_opt, cal, drift_df, doe_df, bud=None):
    wb = Workbook()
    ws = wb.active
    ws.title = "Lisez-moi"
    rows = [("Qualification d'un banc de mesure de fermeté (jumeau numérique)", None),
            ("Toutes les feuilles GRR, Étalonnage et Suivi recalculent les résultats par des formules Excel à partir des mesures brutes.", None),
            ("Pour l'appliquer à un vrai banc : coller les mesures réelles dans les feuilles Mesures_*.", None),
            ("", None), ("% GRR protocole initial", None), ("% GRR protocole optimisé", None)]
    for i, (a, _) in enumerate(rows, 1):
        ws.cell(i, 1, a)
    ws["A1"].font = Font(bold=True, size=13)
    g0 = _grr_sheet(wb, "P0_manuel", df_man)
    g1 = _grr_sheet(wb, "P1_optimise", df_opt)
    ws["B5"], ws["B6"] = f"={g0}", f"={g1}"
    for c in ("B5", "B6"):
        ws[c].number_format = "0.0"
        ws[c].font = BOLD
    ws.column_dimensions["A"].width = 100

    cs = wb.create_sheet("Etalonnage")
    _table(cs, __import__("pandas").DataFrame(cal["points"], columns=["sens", "masse_g", "reference_n", "lecture_n", "ecart_type_n"]))
    n = len(cal["points"]) + 1
    cs["G1"], cs["H1"] = "Gain (pente)", f"=SLOPE(C2:C{n},D2:D{n})"
    cs["G2"], cs["H2"] = "Offset (N)", f"=INTERCEPT(C2:C{n},D2:D{n})"
    cs["F1"] = "erreur corrigée (N)"
    cs["F1"].fill, cs["F1"].font = HEAD, WHITE
    for r in range(2, n + 1):
        cs[f"F{r}"] = f"=$H$1*D{r}+$H$2-C{r}"
        cs[f"F{r}"].number_format = "0.0000"
    cs["G3"], cs["H3"] = "Erreur max (% PE, 5 kg)", f"=100*MAX(ABS(MAX(F2:F{n})),ABS(MIN(F2:F{n})))/(5*9.80665)"
    cs.column_dimensions["G"].width = 26

    ds = wb.create_sheet("Suivi_reference")
    _table(ds, drift_df[["day", "mean_n", "sd_n", "temp_c"]])
    n = len(drift_df) + 1
    ds["F1"], ds["G1"], ds["H1"], ds["I1"] = "EWMA", "LCS", "LCI", "Alarme"
    for c in ("F1", "G1", "H1", "I1"):
        ds[c].fill, ds[c].font = HEAD, WHITE
    ds["K1"], ds["L1"] = "Ligne centrale (15 premiers jours)", "=AVERAGE(B2:B16)"
    ds["K2"], ds["L2"] = "Écart-type de référence", "=_xlfn.STDEV.S(B2:B16)"
    ds["K3"], ds["L3"] = "λ", 0.2
    ds["K4"], ds["L4"] = "L", 3
    ds["K5"], ds["L5"] = "Premier jour en alarme", f'=IFERROR(INDEX(A2:A{n},MATCH("ALARME",I2:I{n},0)),"aucun")'
    for r in range(2, n + 1):
        prev = "$L$1" if r == 2 else f"F{r - 1}"
        ds[f"F{r}"] = f"=$L$3*B{r}+(1-$L$3)*{prev}"
        w = f"$L$4*$L$2*SQRT($L$3/(2-$L$3)*(1-(1-$L$3)^(2*A{r})))"
        ds[f"G{r}"] = f"=$L$1+{w}"
        ds[f"H{r}"] = f"=$L$1-{w}"
        ds[f"I{r}"] = f'=IF(AND(A{r}>15,OR(F{r}>G{r},F{r}<H{r})),"ALARME","")'
    ds.column_dimensions["K"].width = 34
    ch = LineChart()
    ch.title, ch.height, ch.width = "Gel de référence : EWMA et limites", 8, 18
    ch.add_data(Reference(ds, min_col=6, min_row=1, max_col=8, max_row=n), titles_from_data=True)
    ch.set_categories(Reference(ds, min_col=1, min_row=2, max_row=n))
    ds.add_chart(ch, "K8")

    ps = wb.create_sheet("Plan_experiences")
    _table(ps, doe_df.sort_values("grr_pct"))
    for ws_ in wb.worksheets:
        ws_.sheet_view.showGridLines = ws_.title.startswith("Mesures")
        for row in ws_.iter_rows(min_row=1, max_row=1):
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="center")
    if bud:
        uncertainty_sheet(wb, bud)
    wb.save(path)


def uncertainty_sheet(wb, bud):
    """GUM budget with live formulas: change a half-width or a sensitivity and the expanded uncertainty updates."""
    ws = wb.create_sheet("Budget_incertitude", 1)
    ws["A1"] = f"Budget d'incertitude d'une mesure de fermeté (GUM, JCGM 100), protocole optimisé, crème {bud['sample']}"
    ws["A1"].font = Font(bold=True, size=12)
    ws["A3"], ws["B3"] = "Valeur mesurée F (N)", round(bud["F_n"], 4)
    heads = ["Source", "Type", "Loi", "Demi-étendue ou écart-type", "Unité", "Diviseur", "Coefficient de sensibilité (N/unité)",
             "u(F) (N)", "Contribution (%)", "Origine de la valeur"]
    r0 = 5
    for j, h in enumerate(heads, 1):
        c = ws.cell(r0, j, h)
        c.fill, c.font = HEAD, WHITE
        c.alignment = Alignment(wrap_text=True, vertical="center")
    n = len(bud["rows"])
    last = r0 + n
    for i, row in enumerate(bud["rows"], 1):
        r = r0 + i
        ws.cell(r, 1, row["source"])
        ws.cell(r, 2, row["type"])
        ws.cell(r, 3, "normale" if row["distribution"] == "normal" else "rectangulaire")
        ws.cell(r, 4, float(row["value"])).number_format = "0.000000"
        ws.cell(r, 5, row["unit"].replace("relative", "relatif"))
        ws.cell(r, 6, "=SQRT(3)" if row["distribution"] == "rectangular" else 1).number_format = "0.000"
        # sensitivity proportional to F is written as a formula on the measured value
        sens = row["sensitivity"]
        if abs(sens - bud["F_n"]) < 1e-9:
            f = "=$B$3"
        elif abs(sens - 1.5 * bud["F_n"]) < 1e-9:
            f = "=1.5*$B$3"
        elif row["source"].startswith("Température"):
            f = f"={sens / bud['F_n']:.4f}*$B$3"
        else:
            f = float(sens)
        ws.cell(r, 7, f).number_format = "0.0000"
        ws.cell(r, 8, f"=ABS(G{r})*D{r}/F{r}").number_format = "0.0000"
        ws.cell(r, 9, f"=100*H{r}^2/$B${last + 2}^2").number_format = "0.0"
        ws.cell(r, 10, row["note"])
    lines = [("Incertitude-type composée u_c (N)", f"=SQRT(SUMSQ(H{r0 + 1}:H{last}))"),
             ("Facteur d'élargissement k", bud["k"]),
             ("Incertitude élargie U (N), environ 95 %", f"=B{last + 3}*B{last + 2}"),
             ("U relative (%)", f"=100*B{last + 4}/B3"),
             ("U sur la moyenne de 3 points (N)", f"=B{last + 3}*SQRT(B{last + 2}^2-H{r0 + 1}^2+H{r0 + 1}^2/3)"),
             ("Résultat", f'=TEXT(B3,"0.00")&" N ± "&TEXT(B{last + 4},"0.00")&" N (k = 2)"')]
    for i, (lab, f) in enumerate(lines):
        r = last + 2 + i
        ws.cell(r, 1, lab).font = BOLD
        c = ws.cell(r, 2, f)
        c.number_format, c.font, c.fill = "0.000", BOLD, KEY
    ws.cell(last + 9, 1, "Les demi-étendues de type B sont des hypothèses (certificats et fiches techniques à reporter). "
                         "La sensibilité de la crème à la température doit être mesurée avant d'être utilisée.")
    for col, w in zip("ABCDEFGHIJ", (44, 7, 14, 14, 10, 10, 18, 11, 13, 70)):
        ws.column_dimensions[col].width = w
    ws.sheet_view.showGridLines = False


def comparison_workbook(path, rows):
    """Template to confront the digital twin with the real bench: simulated column filled, real column to fill."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Simulation_vs_reel"
    ws["A1"] = "Jumeau numérique contre banc réel : à remplir après les essais du protocole docs/validation_protocol.md"
    ws["A1"].font = Font(bold=True, size=12)
    heads = ["Grandeur", "Unité", "Simulation", "Banc réel", "Écart", "Critère d'acceptation", "Conforme ?"]
    for j, h in enumerate(heads, 1):
        c = ws.cell(3, j, h)
        c.fill, c.font = HEAD, WHITE
    for i, (name, unit, sim, crit_txt, crit_formula) in enumerate(rows, 4):
        ws.cell(i, 1, name)
        ws.cell(i, 2, unit)
        ws.cell(i, 3, sim)
        ws.cell(i, 4).fill = PatternFill("solid", fgColor="FFF6DD")
        ws.cell(i, 5, f'=IF(D{i}="","",D{i}-C{i})').number_format = "0.00"
        ws.cell(i, 6, crit_txt)
        ws.cell(i, 7, f'=IF(D{i}="","à mesurer",IF({crit_formula.format(r=i)},"oui","non : recaler le modèle"))')
    for col, w in zip("ABCDEFG", (48, 10, 12, 12, 10, 40, 26)):
        ws.column_dimensions[col].width = w
    ws.cell(len(rows) + 6, 1, "Si un écart sort du critère, mettre à jour le paramètre correspondant dans src/bench.py "
                               "(gain, bruit, jeu, hétérogénéité, sensibilité thermique) puis relancer src/run_all.py.")
    wb.save(path)
