"""
memo_generator.py
-----------------
Takes the structured analysis from earnings_agent.py
and formats it into a clean hedge-fund-style investment memo.
Saves it as both a .txt file and a .json file.
"""

import json
from pathlib import Path
from datetime import datetime

MEMO_DIR = Path(__file__).parent.parent.parent / "data" / "memos"
MEMO_DIR.mkdir(parents=True, exist_ok=True)


class MemoGenerator:

    def generate(self, analysis: dict) -> dict:
        """
        Takes the analysis dict from EarningsAgent.analyze()
        and returns a formatted memo dict with:
          - memo_text: the full readable memo as a string
          - memo_path: where the .txt file was saved
          - json_path: where the .json file was saved
        """
        ticker = analysis.get("ticker", "UNKNOWN")
        period = analysis.get("period", "")
        date = analysis.get("date", datetime.now().strftime("%Y-%m-%d"))
        signal = analysis.get("overall_signal", "neutral").upper()

        memo_text = self._build_memo(analysis, ticker, period, date, signal)

        # Save files
        safe_period = period.replace(" ", "_")
        txt_path = MEMO_DIR / f"{ticker}_{safe_period}_memo.txt"
        json_path = MEMO_DIR / f"{ticker}_{safe_period}_analysis.json"

        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(memo_text)

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(analysis, f, indent=2)

        print(f"\n[✓] Memo saved to: {txt_path}")
        print(f"[✓] Raw analysis saved to: {json_path}")

        return {
            "memo_text": memo_text,
            "memo_path": str(txt_path),
            "json_path": str(json_path),
        }

    def _build_memo(self, analysis, ticker, period, date, signal) -> str:
        lines = []

        # ------------------------------------------------------------------
        # HEADER
        # ------------------------------------------------------------------
        lines.append("=" * 70)
        lines.append(f"  EARNINGS RESEARCH MEMO")
        lines.append(f"  {ticker} — {period} Earnings Call")
        lines.append(f"  Date: {date}")
        lines.append(f"  Overall Signal: {signal}")
        lines.append("=" * 70)

        # ------------------------------------------------------------------
        # DELTA SUMMARY (only if we had a prior quarter to compare)
        # ------------------------------------------------------------------
        if analysis.get("has_prior_comparison") and analysis.get("delta_summary"):
            lines.append("")
            lines.append("QUARTER-OVER-QUARTER DELTA")
            lines.append("-" * 40)
            lines.append(analysis["delta_summary"])

        # ------------------------------------------------------------------
        # KPIs
        # ------------------------------------------------------------------
        lines.append("")
        lines.append("KEY FINANCIAL METRICS")
        lines.append("-" * 40)

        kpis = analysis.get("kpis", {})

        rev = kpis.get("revenue", {})
        if rev:
            lines.append(f"Revenue:       {rev.get('reported', 'N/A')}")
            lines.append(f"  YoY Growth:  {rev.get('yoy_growth', 'N/A')}")
            lines.append(f"  QoQ Growth:  {rev.get('qoq_growth', 'N/A')}")
            lines.append(f"  vs Guidance: {rev.get('vs_guidance', 'N/A')}")

        gm = kpis.get("gross_margin", {})
        if gm:
            lines.append(f"Gross Margin:  {gm.get('reported', 'N/A')}  ({gm.get('trend', 'N/A')})")
            lines.append(f"  YoY Change:  {gm.get('yoy_change', 'N/A')}")

        op = kpis.get("operating_income", {})
        if op and op.get("reported"):
            lines.append(f"Op. Income:    {op.get('reported', 'N/A')}  (margin: {op.get('margin', 'N/A')})")

        eps = kpis.get("eps", {})
        if eps and eps.get("reported"):
            lines.append(f"EPS:           {eps.get('reported', 'N/A')}  vs consensus: {eps.get('vs_consensus', 'N/A')}")

        segments = kpis.get("segment_highlights", [])
        if segments:
            lines.append("")
            lines.append("Segment Breakdown:")
            for seg in segments:
                lines.append(f"  • {seg.get('name', '')}: {seg.get('revenue', '')}  ({seg.get('growth', '')} YoY)")

        other_metrics = kpis.get("other_key_metrics", [])
        if other_metrics:
            lines.append("")
            lines.append("Other Key Metrics:")
            for m in other_metrics:
                lines.append(f"  • {m.get('metric', '')}: {m.get('value', '')}  — {m.get('context', '')}")

        # ------------------------------------------------------------------
        # GUIDANCE
        # ------------------------------------------------------------------
        lines.append("")
        lines.append("FORWARD GUIDANCE")
        lines.append("-" * 40)

        guidance = analysis.get("guidance", {})
        nq = guidance.get("next_quarter_guidance", {})
        if nq:
            lines.append("Next Quarter:")
            if nq.get("revenue"):
                lines.append(f"  Revenue:    {nq['revenue']}")
            if nq.get("gross_margin"):
                lines.append(f"  Gr. Margin: {nq['gross_margin']}")
            if nq.get("operating_expenses"):
                lines.append(f"  OpEx:       {nq['operating_expenses']}")
            if nq.get("eps"):
                lines.append(f"  EPS:        {nq['eps']}")

        fy = guidance.get("full_year_guidance", {})
        if fy and fy.get("revenue"):
            lines.append(f"Full Year Revenue Guidance: {fy['revenue']}")

        changes = guidance.get("guidance_changes_vs_prior", [])
        if changes:
            lines.append("")
            lines.append("Guidance Changes vs Prior Quarter:")
            for change in changes:
                direction = change.get("direction", "").upper()
                lines.append(
                    f"  [{direction}] {change.get('metric', '')}: "
                    f"{change.get('prior_guidance', '?')} → {change.get('new_guidance', '?')} "
                    f"({change.get('magnitude', '')})"
                )

        notable = guidance.get("notable_guidance_items", [])
        if notable:
            lines.append("")
            lines.append("Notable Guidance Items:")
            for item in notable:
                lines.append(f"  • {item}")

        withdrawn = guidance.get("withdrawn_metrics", [])
        if withdrawn:
            lines.append("")
            lines.append("⚠ Withdrawn / No Longer Guided:")
            for item in withdrawn:
                lines.append(f"  • {item}")

        # ------------------------------------------------------------------
        # MANAGEMENT TONE
        # ------------------------------------------------------------------
        lines.append("")
        lines.append("MANAGEMENT TONE ANALYSIS")
        lines.append("-" * 40)

        tone = analysis.get("tone", {})
        conf_score = tone.get("overall_confidence_score", "N/A")
        lines.append(f"Confidence Score: {conf_score}/10")
        lines.append(f"Rationale: {tone.get('confidence_rationale', 'N/A')}")
        lines.append(f"CEO Tone:   {tone.get('ceo_tone', 'N/A')}")
        lines.append(f"CFO Tone:   {tone.get('cfo_tone', 'N/A')}")

        if tone.get("ceo_cfo_divergence"):
            lines.append(f"⚠ CEO/CFO DIVERGENCE: {tone.get('divergence_note', '')}")

        red_flags = tone.get("red_flags", [])
        if red_flags:
            lines.append("")
            lines.append("Red Flags:")
            for flag in red_flags:
                lines.append(f"  ⚠ {flag}")

        positives = tone.get("standout_positive", [])
        if positives:
            lines.append("")
            lines.append("Strong Conviction Statements:")
            for p in positives:
                lines.append(f"  ✓ {p}")

        hedging = tone.get("hedging_phrases_detected", [])
        if hedging:
            lines.append("")
            lines.append("Hedging Language Detected:")
            for phrase in hedging[:5]:
                lines.append(f"  — \"{phrase}\"")

        # ------------------------------------------------------------------
        # NARRATIVE SHIFTS
        # ------------------------------------------------------------------
        lines.append("")
        lines.append("NARRATIVE SHIFTS")
        lines.append("-" * 40)

        narrative = analysis.get("narrative", {})
        delta = narrative.get("key_narrative_delta", "")
        if delta:
            lines.append(f"Key Delta: {delta}")

        new_topics = narrative.get("new_topics_introduced", [])
        if new_topics:
            lines.append("")
            lines.append("New Topics This Quarter:")
            for t in new_topics:
                lines.append(f"  [NEW] {t.get('topic', '')}")
                lines.append(f"        Significance: {t.get('significance', '')}")

        increasing = narrative.get("topics_increasing_in_emphasis", [])
        if increasing:
            lines.append("")
            lines.append("Topics Growing in Emphasis:")
            for t in increasing:
                lines.append(f"  ↑ {t.get('topic', '')}: {t.get('evidence', '')}")

        dropping = narrative.get("topics_decreasing_or_dropped", [])
        if dropping:
            lines.append("")
            lines.append("Topics Decreasing or Dropped:")
            for t in dropping:
                lines.append(f"  ↓ {t.get('topic', '')}: {t.get('note', '')}")

        risks = narrative.get("risk_factors_mentioned", [])
        if risks:
            lines.append("")
            lines.append("Risk Factors:")
            for r in risks:
                first_time = " [FIRST MENTION]" if r.get("first_time_mentioned") else ""
                lines.append(f"  • {r.get('risk', '')}{first_time}")
                if r.get("management_response"):
                    lines.append(f"    Management: {r['management_response']}")

        # ------------------------------------------------------------------
        # Q&A INTELLIGENCE
        # ------------------------------------------------------------------
        lines.append("")
        lines.append("Q&A INTELLIGENCE")
        lines.append("-" * 40)

        qa = analysis.get("qa_intelligence", {})

        if not qa.get("qa_available", True):
            lines.append("Q&A section not available.")
        else:
            evasiveness = qa.get("evasiveness_score", "N/A")
            lines.append(f"Evasiveness Score: {evasiveness}/10")
            lines.append(f"Key Takeaway: {qa.get('key_qa_takeaway', 'N/A')}")

            concerns = qa.get("top_analyst_concerns", [])
            if concerns:
                lines.append("")
                lines.append("Top Analyst Concerns:")
                for c in concerns:
                    lines.append(
                        f"  • {c.get('concern', '')} "
                        f"— Management response: {c.get('management_response_quality', 'N/A')}"
                    )

            evasions = qa.get("evasiveness_examples", [])
            if evasions:
                lines.append("")
                lines.append("Evasiveness Examples:")
                for e in evasions[:3]:
                    lines.append(f"  ⚠ Topic: {e.get('question_topic', '')}")
                    lines.append(f"    Type: {e.get('evasion_type', '')}")

            credibility = qa.get("management_credibility_signals", [])
            if credibility:
                lines.append("")
                lines.append("Management Credibility Signals:")
                for c in credibility:
                    icon = "✓" if c.get("positive_or_negative") == "positive" else "⚠"
                    lines.append(f"  {icon} {c.get('signal', '')}")

        # ------------------------------------------------------------------
        # FOOTER
        # ------------------------------------------------------------------
        lines.append("")
        lines.append("=" * 70)
        lines.append(f"  Generated by Earnings Research Agent")
        lines.append(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("=" * 70)

        return "\n".join(lines)
