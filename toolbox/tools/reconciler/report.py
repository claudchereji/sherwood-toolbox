"""Render a Recon result to a Markdown report and a CSV line-item diff."""

from __future__ import annotations

import csv
import os


def _money(x):
    return f"${x:,.2f}"


def write_csv(recon, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["status", "category", "description", "quantity", "unit",
                    "carrier_unit_price", "contractor_unit_price",
                    "est_dollars", "confidence", "note"])
        for s in recon.suggestions:
            w.writerow([s.status, s.category, s.description,
                        f"{s.quantity:g}", s.unit,
                        f"{s.carrier_unit_price:.2f}", f"{s.contractor_unit_price:.2f}",
                        f"{s.dollars:.2f}", s.confidence, s.note])
    return path


def _bridge_reconciled(b):
    rows = [
        ("Carrier estimate RCV (as written)", b["carrier_rcv"], ""),
        ("+ Missing line items (contractor scope carrier omits)", b["missing_base"],
         "base pricing, before O&P"),
        ("+/- Net price & quantity difference on shared items", b["matched_delta"],
         "positive = contractor higher"),
        ("- Items only in the carrier estimate", -b["carrier_only_base"],
         "scope the contractor dropped"),
        ("+ Overhead & Profit gap", b["op_gap"], "contractor O&P minus carrier O&P"),
        ("+ Sales tax gap", b["tax_gap"], ""),
    ]
    lines = ["| Component | Amount | Note |", "|---|---:|---|"]
    for label, amt, note in rows:
        lines.append(f"| {label} | {_money(amt)} | {note} |")
    lines.append(f"| **= Reconciled contractor RCV (predicted)** | "
                 f"**{_money(b['predicted_contractor_rcv'])}** | |")
    lines.append(f"| Actual contractor RCV | {_money(b['actual_contractor_rcv'])} | |")
    lines.append(f"| Unreconciled residual | {_money(b['residual'])} | "
                 f"parse/rounding noise |")
    pct = abs(b["residual"]) / b["total_gap"] * 100 if b["total_gap"] else 0
    lines.append("")
    lines.append(f"**Total RCV gap: {_money(b['total_gap'])}** "
                 f"(residual {pct:.1f}% of gap).")
    return "\n".join(lines)


def _bridge_estimated(b):
    lines = ["| Component | Amount |", "|---|---:|",
             f"| Carrier estimate RCV | {_money(b['carrier_rcv'])} |",
             f"| + Suggested missing items (playbook medians) | {_money(b['suggested_items'])} |",
             f"| + Overhead & Profit estimate | {_money(b['op_estimate'])} |",
             f"| **= Projected supported RCV** | **{_money(b['projected_supported_rcv'])}** |",
             "",
             f"**Projected uplift: {_money(b['uplift'])}** (estimate; no contractor "
             "file to reconcile against)."]
    return "\n".join(lines)


def _sugg_table(recon, statuses, title, cols_note="Est. $ (RCV)"):
    rows = [s for s in recon.suggestions if s.status in statuses]
    if not rows:
        return ""
    out = [f"### {title}", "",
           f"| Category | Item | Qty | Unit | {cols_note} | Conf. |",
           "|---|---|---:|---|---:|---|"]
    for s in rows:
        q = f"{s.quantity:g}" if s.status != "MISSING_OP" else "-"
        u = s.unit if s.status != "MISSING_OP" else "-"
        out.append(f"| {s.category} | {s.description} | {q} | {u} | "
                   f"{_money(s.dollars)} | {s.confidence} |")
    out.append("")
    return "\n".join(out)


def render_markdown(recon):
    L = []
    L.append(f"# Reconciliation: {recon.claimant}")
    L.append("")
    L.append(f"- Carrier estimate: `{recon.carrier_name}`  "
             f"(RCV {_money(recon.carrier_grand)}, O&P applied: "
             f"{'yes' if recon.carrier_has_op else 'NO'})")
    if recon.mode == "reconciled":
        L.append(f"- Contractor file: `{recon.contractor_name}`  "
                 f"(RCV {_money(recon.contractor_grand)}, O&P applied: "
                 f"{'yes' if recon.contractor_has_op else 'no'})")
    L.append(f"- Mode: **{recon.mode}**")
    L.append(f"- Estimated recoverable: **{_money(recon.est_recoverable)}**")
    L.append("")
    for n in recon.notes:
        L.append(f"> Note: {n}")
        L.append("")

    L.append("## RCV reconciliation")
    L.append("")
    L.append(_bridge_reconciled(recon.bridge) if recon.mode == "reconciled"
             else _bridge_estimated(recon.bridge))
    L.append("")

    L.append("## Suggested additions")
    L.append("")
    if recon.mode == "reconciled":
        t = _sugg_table(recon, ("MISSING",), "Missing line items "
                        "(in contractor scope, absent from carrier)")
        L.append(t if t else "_No missing line items detected._\n")
    else:
        t = _sugg_table(recon, ("SUGGESTED",), "Playbook items likely missing")
        L.append(t if t else "_No playbook items flagged._\n")

    op = _sugg_table(recon, ("MISSING_OP",), "Overhead & Profit")
    if op:
        L.append(op)

    info = _sugg_table(recon, ("INFO",),
                       "Informational: shared items priced higher by contractor",
                       cols_note="Est. $ delta")
    if info:
        L.append(info)

    return "\n".join(L)


def render_summary(recons):
    L = ["# Reconciliation summary", "",
         "One row per carrier estimate. Recoverable = additional RCV the "
         "contractor scope (or playbook) supports beyond the carrier.", "",
         "| Claimant | Mode | Carrier RCV | Contractor RCV | Recoverable | "
         "Carrier O&P | Missing items | Conf. |",
         "|---|---|---:|---:|---:|:---:|---:|:---:|"]
    total_rec = 0.0
    for r in sorted(recons, key=lambda x: -x.est_recoverable):
        n_missing = sum(1 for s in r.suggestions if s.status in ("MISSING", "SUGGESTED"))
        contr = _money(r.contractor_grand) if r.mode == "reconciled" else "-"
        conf = "low" if (r.carrier_ocr or r.carrier_conf == "low") else r.carrier_conf
        L.append(f"| {r.claimant} | {r.mode} | {_money(r.carrier_grand)} | {contr} | "
                 f"{_money(r.est_recoverable)} | "
                 f"{'yes' if r.carrier_has_op else 'NO'} | {n_missing} | {conf} |")
        total_rec += r.est_recoverable
    L.append(f"| **Total** | | | | **{_money(total_rec)}** | | | |")
    L.append("")
    L.append("- **reconciled**: a same-claimant contractor file exists; the "
             "recoverable is the exact RCV gap between the two files.")
    L.append("- **estimated**: no contractor file; the recoverable is a playbook "
             "projection and depends on this claim's real measurements.")
    L.append("")

    caveats = []
    for r in recons:
        if r.carrier_ocr:
            caveats.append(f"- **{r.claimant}**: carrier is an image-only scan; "
                           "OCR figures are approximate, verify against the PDF.")
        elif r.mode == "estimated" and r.carrier_grand < 5000:
            caveats.append(f"- **{r.claimant}**: small carrier estimate "
                           f"({_money(r.carrier_grand)}); playbook dollars assume a "
                           "full claim and likely overstate this one.")
    if caveats:
        L.append("## Caveats")
        L.append("")
        L.extend(sorted(set(caveats)))
        L.append("")
    return "\n".join(L)


def write_report(recon, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    safe = recon.claimant.replace(" ", "_").replace("/", "-")
    md = os.path.join(out_dir, f"{safe}.md")
    csvp = os.path.join(out_dir, f"{safe}.csv")
    with open(md, "w", encoding="utf-8") as f:
        f.write(render_markdown(recon))
    write_csv(recon, csvp)
    return md, csvp
