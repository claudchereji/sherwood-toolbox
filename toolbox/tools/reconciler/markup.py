"""Turn a Recon into a marked-up carrier estimate PDF.

The reconciler engine already finds the difference between the two estimates.
This module paints that difference back onto the carrier's own PDF so a reviewer
sees it in place instead of reading it off a table:

  * a prepended **summary page** with the headline numbers and a colour legend;
  * **in-line flags** on the carrier's line items the contractor measured higher,
    each highlighted across the line with a numbered tab in the left margin,
    coloured by how large the quantity gap is in dollars;
  * appended **detail pages** that decode every flag, list the scope the carrier
    left out entirely (grouped by category), show the RCV build-up, and quote the
    carrier's own coverage statements and the denial hypotheses.

Everything is drawn with PyMuPDF (`fitz`); there is no other dependency. Nothing
here reads the network or the filesystem beyond the one carrier PDF it is handed.

Line items are located by re-clustering the page words into rows and matching the
row whose leading token is the printed line number (see `locate_items`). An
image-only carrier has no text layer to locate against, so in-line flags are
skipped and the appended pages still carry the full picture.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import fitz

# === SECTION: palette ===
# Brand tokens from toolbox.css, as RGB in 0..1. The estimate stays black on
# white; markup adds these on top.
INK = (0.11, 0.14, 0.11)         # --ink   #1c241c
MUTED = (0.36, 0.42, 0.35)       # --muted #5d6b5a
GREEN = (0.13, 0.24, 0.14)       # --green-800 #203c23
GREEN_MID = (0.29, 0.49, 0.32)   # --green-500 #4a7c52
LINE = (0.80, 0.84, 0.76)        # --line  #cdd6c2
SAGE = (0.93, 0.95, 0.90)        # --sage-50 #eef1e6
WHITE = (1, 1, 1)

# Severity of a quantity shortfall, keyed by its dollar size (contractor unit
# price x the quantity the carrier is short). Heuristic thresholds; the band fill
# is drawn translucent so the line item reads through it.
@dataclass(frozen=True)
class Severity:
    label: str
    fill: tuple      # highlight band + flag colour
    floor: float     # dollar impact at or above which this level applies


SEVERITIES = (
    Severity("major", (0.86, 0.24, 0.20), 500.0),    # red
    Severity("moderate", (0.90, 0.56, 0.15), 150.0),  # amber
    Severity("minor", (0.85, 0.72, 0.15), 0.0),       # yellow
)


def severity_for(dollars: float) -> Severity:
    for s in SEVERITIES:
        if dollars >= s.floor:
            return s
    return SEVERITIES[-1]


# === SECTION: money / number formatting ===
def _money(x) -> str:
    return ("-" if x < 0 else "") + f"${abs(x):,.2f}"


def _signed_money(x) -> str:
    return ("+" if x >= 0 else "-") + f"${abs(x):,.2f}"


def _qty(x) -> str:
    return f"{x:g}"


def _signed_qty(x) -> str:
    return ("+" if x >= 0 else "") + f"{x:g}"


# === SECTION: locating line-item rows on the carrier pages ===
ROW_OVERLAP = 0.5          # min vertical overlap to treat two words as one row
_LEADING_NUM = re.compile(r"^(\d{1,3})\.?$")   # '1.' (Xactimate) or '7' (Symbility)


def _cluster_rows(page):
    """Group a page's words into visual rows by vertical overlap.

    Mirrors extract._page_layout_text's clustering but keeps each row's word
    rectangles so a located row can be drawn on. Returns rows sorted top-to-
    bottom, each a dict with y-span, the ordered words, and the joined text.
    """
    words = page.get_text("words")
    if not words:
        return []
    words.sort(key=lambda w: (round(w[1], 1), w[0]))
    rows = []
    for w in words:
        y0, y1 = w[1], w[3]
        best, best_ov = None, 0.0
        for row in rows:
            ov = min(y1, row["y1"]) - max(y0, row["y0"])
            h = min(y1 - y0, row["y1"] - row["y0"])
            frac = ov / h if h > 0 else 0.0
            if frac > best_ov:
                best, best_ov = row, frac
        if best is not None and best_ov >= ROW_OVERLAP:
            best["ws"].append(w)
            best["y0"] = min(best["y0"], y0)
            best["y1"] = max(best["y1"], y1)
        else:
            rows.append({"y0": y0, "y1": y1, "ws": [w]})
    rows.sort(key=lambda r: r["y0"])
    for r in rows:
        r["ws"].sort(key=lambda w: w[0])
        r["x0"] = r["ws"][0][0]
        r["x1"] = r["ws"][-1][2]
        r["text"] = " ".join(w[4] for w in r["ws"])
    return rows


def _row_keyword(description: str) -> str:
    """First distinctive word of a line-item description, lower-cased, for a
    sanity check that a number-matched row is really that item and not a stray
    leading integer. Skips the action prefix and short filler tokens."""
    for tok in re.split(r"[^A-Za-z]+", description.lower()):
        if len(tok) >= 4 and tok not in ("remove", "detach", "reset", "replace"):
            return tok
    for tok in re.split(r"[^A-Za-z]+", description.lower()):
        if len(tok) >= 3:
            return tok
    return ""


def locate_items(doc, wanted):
    """Map each wanted carrier line number to its row rect on the page.

    `wanted` is {line_number: description}. A row matches when its first token is
    that number and the item's keyword appears in the row text (guarding against
    a recap row or page number that merely starts with the same integer). The
    first match wins; line numbers are unique per estimate. Returns
    {line_number: (page_index, fitz.Rect)}.
    """
    found = {}
    for pno in range(len(doc)):
        rows = _cluster_rows(doc.load_page(pno))
        for row in rows:
            m = _LEADING_NUM.match(row["ws"][0][4])
            if not m:
                continue
            num = int(m.group(1))
            if num not in wanted or num in found:
                continue
            kw = _row_keyword(wanted[num])
            if kw and kw not in row["text"].lower():
                continue
            found[num] = (pno, fitz.Rect(row["x0"], row["y0"], row["x1"], row["y1"]))
    return found


# === SECTION: in-line flags on the carrier pages ===
FLAG_L = 6.0               # left-margin flag x-range
FLAG_R = 30.0
BAND_INSET = 34.0         # highlight band left/right inset from the page edge


def flag_row(page, rect, marker: int, sev: Severity):
    """Highlight a located line item across its width and drop a numbered tab in
    the left margin. The band is translucent so the priced line reads through."""
    w = page.rect.width
    band = fitz.Rect(BAND_INSET, rect.y0 - 1.5, w - BAND_INSET, rect.y1 + 1.5)
    page.draw_rect(band, color=None, fill=sev.fill, fill_opacity=0.22)
    # thin left rule at the band edge for definition
    page.draw_line(fitz.Point(BAND_INSET, band.y0), fitz.Point(BAND_INSET, band.y1),
                   color=sev.fill, width=1.4)
    # left-margin numbered tab (solid), only where the gutter is wide enough
    if rect.x0 >= FLAG_R + 2:
        tab = fitz.Rect(FLAG_L, rect.y0 - 1.0, FLAG_R, rect.y1 + 1.0)
        page.draw_rect(tab, color=None, fill=sev.fill, fill_opacity=1.0, radius=0.25)
        label = str(marker)
        tw = fitz.get_text_length(label, "hebo", 8)
        cx = FLAG_L + (FLAG_R - FLAG_L - tw) / 2
        cy = (rect.y0 + rect.y1) / 2 + 2.9
        page.insert_text(fitz.Point(cx, cy), label, fontname="hebo", fontsize=8,
                         color=WHITE)


# === SECTION: page canvas for the summary and detail pages ===
PAGE_W, PAGE_H = 612.0, 792.0
MARGIN = 54.0


class Canvas:
    """A running-cursor writer over appended letter pages. Text is laid out top
    to bottom; `space` breaks to a new page when the current one is full.

    Single lines are drawn with `insert_text` at a computed baseline, never with
    `insert_textbox`: a textbox silently renders nothing when its one line is a
    hair too tall for the box, which is easy to trip into with tight table rows.
    Only genuinely wrapped paragraphs (`text`, `quote`) use a textbox, with the
    full remaining page height so nothing clips.
    """

    def __init__(self, doc, at_front=False, single_page=False):
        # single_page: never break to a new page. The prepended summary must stay
        # exactly one page, or the mark_up_carrier page math (original page ->
        # final page = index + 2) would be off and an overflow page would land at
        # the very end instead of after the summary.
        self.doc = doc
        self.single_page = single_page
        self.page = doc.new_page(0 if at_front else -1, width=PAGE_W, height=PAGE_H)
        self.y = MARGIN
        self.pages = 1

    def _new_page(self):
        self.page = self.doc.new_page(-1, width=PAGE_W, height=PAGE_H)
        self.y = MARGIN
        self.pages += 1

    def space(self, h):
        if not self.single_page and self.y + h > PAGE_H - MARGIN:
            self._new_page()

    def _line(self, x, s, size, font="helv", color=INK, align=0, box_w=None):
        """Draw one line; baseline sits `size` below the current top. `align` is
        0 left, 1 centre, 2 right within `box_w` (from x)."""
        if align and box_w is not None:
            tw = fitz.get_text_length(s, font, size)
            x = x + box_w - tw if align == 2 else x + (box_w - tw) / 2
        self.page.insert_text(fitz.Point(x, self.y + size), s, fontname=font,
                              fontsize=size, color=color)

    def text(self, s, size=10, font="helv", color=INK, x=MARGIN, gap=4, width=None):
        width = width or (PAGE_W - 2 * MARGIN)
        lines = max(1, self._wrapped_lines(s, font, size, width))
        h = lines * (size + 2)
        self.space(h)
        rect = fitz.Rect(x, self.y, x + width, PAGE_H - MARGIN)
        self.page.insert_textbox(rect, s, fontname=font, fontsize=size, color=color,
                                 align=fitz.TEXT_ALIGN_LEFT)
        self.y += h + gap

    @staticmethod
    def _wrapped_lines(s, font, size, width):
        n = 0
        for para in s.split("\n"):
            words, line = para.split(" "), ""
            if not words:
                n += 1
                continue
            count = 1
            for wd in words:
                trial = (line + " " + wd).strip()
                if fitz.get_text_length(trial, font, size) > width and line:
                    count += 1
                    line = wd
                else:
                    line = trial
            n += count
        return n

    def rule(self, color=LINE, gap=8):
        self.space(gap + 2)
        self.page.draw_line(fitz.Point(MARGIN, self.y), fitz.Point(PAGE_W - MARGIN, self.y),
                            color=color, width=0.8)
        self.y += gap

    def heading(self, s, size=15):
        self.space(size + 12)
        self._line(MARGIN, s, size, font="hebo", color=GREEN)
        self.y += size + 8

    def subheading(self, s, color=GREEN):
        self.space(20)
        self._line(MARGIN, s, 10.5, font="hebo", color=color)
        self.y += 17

    def row(self, cells, widths, *, font="helv", size=9.5, color=INK, aligns=None,
            head=False, fill=None):
        """One table row. `cells` and `widths` are parallel; `widths` sum to the
        content width. `aligns` is per-column (0 left, 1 centre, 2 right)."""
        h = size + 7
        self.space(h)
        aligns = aligns or [0] * len(cells)
        if fill:
            self.page.draw_rect(fitz.Rect(MARGIN, self.y, PAGE_W - MARGIN, self.y + h),
                                color=None, fill=fill, fill_opacity=1.0)
        top = self.y
        x = MARGIN
        fnt = "hebo" if head else font
        pad = 4
        for cell, wdt, al in zip(cells, widths, aligns):
            s = _fit(str(cell), fnt, size, wdt - 2 * pad)
            self.y = top + 1
            self._line(x + pad, s, size, font=fnt, color=color, align=al,
                       box_w=wdt - 2 * pad)
            x += wdt
        self.y = top + h

    def quote(self, s):
        """An indented, rule-bordered verbatim quote block."""
        inner = PAGE_W - 2 * MARGIN - 18
        lines = self._wrapped_lines(s, "helv", 9, inner)
        h = lines * 11 + 10
        self.space(h)
        top = self.y
        self.page.draw_rect(fitz.Rect(MARGIN, top, PAGE_W - MARGIN, top + h),
                            color=None, fill=SAGE, fill_opacity=1.0)
        self.page.draw_line(fitz.Point(MARGIN, top), fitz.Point(MARGIN, top + h),
                            color=GREEN_MID, width=2.2)
        self.page.insert_textbox(fitz.Rect(MARGIN + 12, top + 5, PAGE_W - MARGIN - 6,
                                           top + h), s, fontname="helv", fontsize=9,
                                 color=INK, align=fitz.TEXT_ALIGN_LEFT)
        self.y = top + h + 6


def _fit(s, font, size, width):
    """Ellipsize a cell string to fit a column width (ASCII '...' so it renders
    in the Base-14 fonts, which lack a real ellipsis glyph)."""
    if fitz.get_text_length(s, font, size) <= width:
        return s
    while s and fitz.get_text_length(s + "...", font, size) > width:
        s = s[:-1]
    return s.rstrip() + "..."


# === SECTION: summary page (prepended) ===
def _summary_page(doc, recon, flagged, missing, located_count):
    c = Canvas(doc, at_front=True, single_page=True)
    c.heading(f"Reconciliation summary - {recon.claimant}", size=17)
    c.text("Carrier estimate marked up against the contractor scope. Details are "
           "flagged in place on the following pages and listed at the back.",
           size=9.5, color=MUTED, gap=10)

    # Headline recoverable, boxed
    box_h = 48
    c.space(box_h + 6)
    top = c.y
    c.page.draw_rect(fitz.Rect(MARGIN, top, PAGE_W - MARGIN, top + box_h),
                     color=None, fill=SAGE, fill_opacity=1.0)
    c.page.draw_line(fitz.Point(MARGIN, top), fitz.Point(MARGIN, top + box_h),
                     color=GREEN, width=3)
    c.page.insert_text(fitz.Point(MARGIN + 14, top + 17), "ESTIMATED RECOVERABLE",
                       fontname="hebo", fontsize=8.5, color=MUTED)
    c.page.insert_text(fitz.Point(MARGIN + 14, top + 40), _money(recon.est_recoverable),
                       fontname="hebo", fontsize=20, color=GREEN)
    c.y = top + box_h + 12

    # Three totals
    third = (PAGE_W - 2 * MARGIN) / 3
    c.row(["Carrier RCV", "Contractor RCV", "RCV gap"], [third] * 3,
          head=True, size=9, color=MUTED)
    c.row([_money(recon.carrier_grand), _money(recon.contractor_grand),
           _signed_money(round(recon.contractor_grand - recon.carrier_grand, 2))],
          [third] * 3, font="hebo", size=12, color=GREEN)
    c.text(f"Carrier: {recon.carrier_name}", size=8, color=MUTED, gap=1)
    c.text(f"Contractor: {recon.contractor_name}", size=8, color=MUTED, gap=8)

    missing_dollars = round(sum(s.dollars for s in missing), 2)
    op = ("Carrier Overhead & Profit: " +
          ("applied" if recon.carrier_has_op else "NOT applied") +
          f".   Contractor: {'applied' if recon.contractor_has_op else 'not applied'}.")
    c.text(op, size=9.5, gap=10)

    c.rule()
    c.subheading("What the markup shows")
    c.text(f"-  {len(missing)} line items totalling {_money(missing_dollars)} are in "
           f"the contractor scope and absent from this estimate. They are listed by "
           f'category under "Missing scope" at the back.', size=10, gap=6)
    c.text(f"-  {len(flagged)} shared line items are measured higher by the "
           f"contractor; {located_count} are highlighted in place on the pages that "
           f'follow, keyed to the "Quantity differences" table.', size=10, gap=6)
    if flagged and located_count < len(flagged):
        c.text("   Items that could not be located on the page (an image-only scan, "
               "or a line layout the reader did not match) are not highlighted in "
               "place but are still listed in that table.", size=9, color=MUTED,
               gap=10)
    else:
        c.y += 4

    c.subheading("Legend")
    _legend_row(c, SEVERITIES[0].fill, "Major quantity gap ($500 or more short)")
    _legend_row(c, SEVERITIES[1].fill, "Moderate gap ($150 to $500 short)")
    _legend_row(c, SEVERITIES[2].fill, "Minor gap (under $150 short)")
    c.text("A numbered tab in the left margin marks each flagged line; the same "
           "number appears in the Quantity differences table.", size=8.5,
           color=MUTED, gap=6)

    for n in recon.notes:
        c.text(f"Note: {n}", size=8.5, color=MUTED, gap=4)
    c.rule()
    c.text("An aid to review, not a guarantee of coverage. Figures are read from "
           "the two PDFs as printed. Sherwood Estimates (c) 2026.",
           size=8, color=MUTED)


def _legend_row(c, fill, label):
    c.space(16)
    top = c.y
    sw = fitz.Rect(MARGIN, top + 1, MARGIN + 22, top + 12)
    c.page.draw_rect(sw, color=None, fill=fill, fill_opacity=0.5)
    c.page.draw_rect(sw, color=fill, width=0.8)
    c.page.insert_text(fitz.Point(MARGIN + 30, top + 10.5), label, fontname="helv",
                       fontsize=9.5, color=INK)
    c.y = top + 16


# === SECTION: detail pages (appended) ===
_STATEMENT_LABELS = {
    "MATCHING": "Matching exclusion",
    "DEPRECIATION_ACV": "Depreciation / actual cash value",
    "ORDINANCE_CODE": "Ordinance or law / code",
    "POLICY_EXCLUSION": "Policy exclusions",
}
_THEME_TITLES = {"MATCHING": "Matching", "CODE": "Code / ordinance",
                 "UNEXPLAINED": "No stated reason"}


def _detail_pages(doc, recon, flagged, missing, page_of):
    c = Canvas(doc)   # first appended page

    # --- Quantity differences (decodes the in-line flags) ---
    c.heading("Quantity differences")
    c.text("Line items both estimates carry where the contractor measured a higher "
           "quantity than the carrier. The tab number matches the flag on the "
           "carrier page named in the last column.", size=9, color=MUTED, gap=8)
    if flagged:
        cols = [30, 150, 52, 52, 52, 78, 42]
        heads = ["#", "Item", "Carrier", "Contr.", "Diff qty", "RCV gap", "Page"]
        aligns = [1, 0, 2, 2, 2, 2, 2]
        c.row(heads, cols, head=True, size=8.5, color=GREEN, fill=SAGE, aligns=aligns)
        for i, f in enumerate(flagged, start=1):
            loc = page_of.get(f.carrier_number)
            pref = f"p.{loc}" if loc else "-"
            unit = f.unit or ""
            c.row([str(i), f.description,
                   f"{_qty(f.carrier_quantity)} {unit}".strip(),
                   f"{_qty(f.contractor_quantity)} {unit}".strip(),
                   _signed_qty(f.quantity_delta),
                   _signed_money(round(f.quantity_delta * f.contractor_unit_price, 2)),
                   pref], cols, size=8.5, aligns=aligns)
    else:
        c.text("None: no shared line item is measured higher by the contractor.",
               size=9.5, color=MUTED)

    # --- Missing scope, grouped by category ---
    c.rule(gap=12)
    c.heading("Missing scope")
    c.text("In the contractor scope, absent from the carrier estimate. Grouped by "
           "category, largest RCV first; RCV is the value printed in the contractor "
           "estimate.", size=9, color=MUTED, gap=8)
    if missing:
        cols = [34, 250, 66, 40, 78]
        aligns = [1, 0, 2, 0, 2]
        cat = None
        for s in missing:
            if s.category != cat:
                cat = s.category
                sub = round(sum(x.dollars for x in missing if x.category == cat), 2)
                c.subheading(f"{cat}   -   {_money(sub)}")
                c.row(["#", "Item", "Qty", "Unit", "RCV"], cols, head=True, size=8.5,
                      color=GREEN, fill=SAGE, aligns=aligns)
            c.row([str(s.number or ""), s.description, _qty(s.quantity), s.unit,
                   _money(s.dollars)], cols, size=8.5, aligns=aligns)
    else:
        c.text("None: the carrier estimate carries every contractor line item.",
               size=9.5, color=MUTED)

    _bridge_section(c, recon)
    _hypotheses_section(c, recon)
    _statements_section(c, recon)
    return c.pages


def _bridge_section(c, recon):
    b = recon.bridge
    if not b:
        return
    c.rule(gap=12)
    c.heading("RCV build-up")
    c.text("How the carrier RCV bridges to the contractor RCV. A small residual "
           "means the two files are fully reconciled.", size=9, color=MUTED, gap=8)
    labels = [
        ("Carrier RCV", b.get("carrier_rcv")),
        ("+ Missing line items", b.get("missing_base")),
        ("+ Quantity / price delta on shared items", b.get("matched_delta")),
        ("- Items only the carrier carries", b.get("carrier_only_base")),
        ("+ Overhead & Profit gap", b.get("op_gap")),
        ("+ Sales tax gap", b.get("tax_gap")),
        ("= Predicted contractor RCV", b.get("predicted_contractor_rcv")),
        ("Actual contractor RCV", b.get("actual_contractor_rcv")),
        ("Residual (unexplained)", b.get("residual")),
    ]
    w = [PAGE_W - 2 * MARGIN - 110, 110]
    for lab, val in labels:
        if val is None:
            continue
        emph = lab.startswith(("=", "Actual"))
        c.row([lab, _money(val)], w, size=9.5, aligns=[0, 2],
              font="hebo" if emph else "helv",
              color=GREEN if emph else INK)


def _hypotheses_section(c, recon):
    if not recon.hypotheses:
        return
    c.rule(gap=12)
    c.heading("Denial hypotheses")
    c.text('Why scope may be missing. "Quoted exclusion" is backed by the '
           "carrier's own words; \"Inference\" is a guess to verify with the "
           "carrier.", size=9, color=MUTED, gap=8)
    for h in recon.hypotheses:
        title = _THEME_TITLES.get(h.theme, h.theme)
        head = title if title == h.label else f"{title} - {h.label}"
        c.subheading(f"{head}   ({_money(h.dollars)})",
                     color=GREEN if h.basis == "quoted" else (0.42, 0.35, 0.11))
        c.text(h.note, size=9.5, gap=4)
        if h.statement:
            c.quote(h.statement)
        nums = ", ".join(f"#{n}" for n in h.item_numbers)
        c.text(f"Contractor line items: {nums}", size=8.5, color=MUTED, gap=8)


def _statements_section(c, recon):
    if not recon.carrier_statements:
        return
    c.rule(gap=12)
    c.heading("Carrier coverage statements")
    c.text("Quoted verbatim from the carrier estimate.", size=9, color=MUTED, gap=8)
    by_kind = {}
    for s in recon.carrier_statements:
        by_kind.setdefault(s["kind"], []).append(s["text"])
    for kind, texts in by_kind.items():
        c.subheading(_STATEMENT_LABELS.get(kind, kind))
        for t in texts[:5]:
            c.quote(t)
        if len(texts) > 5:
            c.text(f"(+{len(texts) - 5} more)", size=8.5, color=MUTED, gap=6)


# === SECTION: entry point ===
def mark_up_carrier(carrier_path: str, recon, out_path: str) -> dict:
    """Write a marked-up copy of the carrier PDF to `out_path`.

    Returns a stats dict for logging: how many quantity gaps were flagged, how
    many were located and highlighted in place, how many missing items were
    listed, and the total page count added.
    """
    flagged = sorted(
        (s for s in recon.shared if s.quantity_delta > 1e-6),
        key=lambda s: -(s.quantity_delta * s.contractor_unit_price))
    missing = [s for s in recon.suggestions if s.status == "MISSING"]

    doc = fitz.open(carrier_path)
    orig_pages = len(doc)

    wanted = {s.carrier_number: s.description for s in flagged if s.carrier_number}
    located = locate_items(doc, wanted) if wanted else {}
    marker_by_num = {f.carrier_number: i for i, f in enumerate(flagged, start=1)}

    # Highlight each located line item in place, coloured by its dollar shortfall.
    for num, (pno, rect) in located.items():
        f = next(s for s in flagged if s.carrier_number == num)
        sev = severity_for(f.quantity_delta * f.contractor_unit_price)
        flag_row(doc.load_page(pno), rect, marker_by_num[num], sev)

    # A located item's final 1-based page = its original index, + 1 for the single
    # summary page prepended below, + 1 to make it 1-based. Appending the detail
    # pages does not move the original pages, so this holds.
    page_of = {num: pno + 2 for num, (pno, _r) in located.items()}
    detail_pages = _detail_pages(doc, recon, flagged, missing, page_of)
    _summary_page(doc, recon, flagged, missing, located_count=len(located))

    doc.save(out_path, garbage=4, deflate=True)
    doc.close()

    return {
        "flagged": len(flagged),
        "located": len(located),
        "missing": len(missing),
        "missing_dollars": round(sum(s.dollars for s in missing), 2),
        "orig_pages": orig_pages,
        "added_pages": 1 + detail_pages,
    }
