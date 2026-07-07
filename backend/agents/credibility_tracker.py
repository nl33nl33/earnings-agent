"""
credibility_tracker.py
----------------------
Tracks management guidance accuracy over time.
Every time a company reports earnings, we record what
they guided for last quarter and whether they hit it.

Now persisted in Supabase — survives redeploys permanently.
"""

import json
from datetime import datetime
from typing import Optional
from supabase import create_client, Client
import os
from dotenv import load_dotenv

from backend.agents.financial_parser import parse_financial_value

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# When guidance is a single point estimate (not a range), how close does
# the actual result need to be to still count as "in-line" rather than a
# beat or a miss? Expressed as a fraction of the guided value. No company
# hits a single guided number exactly to the decimal, so without a
# tolerance band, tiny rounding differences would get misclassified as
# beats or misses -- manufacturing false signal instead of removing it.
POINT_ESTIMATE_TOLERANCE = {
    "Revenue": 0.01,        # within 1% of the guided figure
    "EPS": 0.02,            # within 2% -- small dollar amounts move % more
    "Gross Margin": 0.005,  # within 0.5 percentage points (50 bps)
}
DEFAULT_TOLERANCE = 0.01


class CredibilityTracker:

    def record(self, ticker: str, analysis: dict):
        """
        Called after every analysis. Records:
        1. What management guided for THIS quarter (saves for future comparison)
        2. How accurate LAST quarter's guidance was vs what was actually reported
        """
        ticker = ticker.upper()
        period = analysis.get("period", "")
        year, quarter = self._parse_period(period)
        date = analysis.get("date", datetime.now().strftime("%Y-%m-%d"))
        guidance = analysis.get("guidance", {})
        kpis = analysis.get("kpis", {})

        guidance_given = self._extract_guidance_given(guidance)
        actuals = self._extract_actuals(kpis)

        # Score prior quarter's guidance against this quarter's actuals
        prior = self._get_prior_record(ticker, year, quarter)
        accuracy_scores = []
        overall_accuracy = None

        if prior:
            prior_guidance = prior.get("guidance_given") or {}
            if isinstance(prior_guidance, str):
                prior_guidance = json.loads(prior_guidance)

            accuracy_scores = self._score_guidance(
                prior_guidance,
                actuals,
                prior.get("period", ""),
                period
            )
            overall_accuracy = self._compute_overall(accuracy_scores)

            # Update prior record with accuracy scores
            self._update_accuracy(
                prior["id"],
                accuracy_scores,
                overall_accuracy
            )

        # Save this quarter's record
        self._save_record(
            ticker=ticker,
            year=year,
            quarter=quarter,
            period=period,
            date=date,
            guidance_given=guidance_given,
            actuals=actuals,
            accuracy_scores=accuracy_scores,
            overall_accuracy=overall_accuracy,
        )

        return self.get_history(ticker)

    def get_history(self, ticker: str) -> dict:
        """Get full credibility history for a ticker."""
        try:
            result = (
                supabase.table("credibility_records")
                .select("*")
                .eq("ticker", ticker.upper())
                .order("year", desc=False)
                .order("quarter", desc=False)
                .execute()
            )
            records = result.data or []

            # Parse JSON fields
            for r in records:
                for field in ["guidance_given", "actuals", "accuracy_scores"]:
                    if isinstance(r.get(field), str):
                        try:
                            r[field] = json.loads(r[field])
                        except Exception:
                            r[field] = {}

            overall_credibility = self._compute_credibility_score({"records": records})

            return {
                "ticker": ticker.upper(),
                "records": records,
                "overall_credibility": overall_credibility,
                "last_updated": records[-1]["created_at"] if records else None,
            }
        except Exception as e:
            print(f"[credibility] Failed to get history: {e}")
            return {"ticker": ticker, "records": [], "overall_credibility": None}

    def get_all_tickers(self) -> list:
        """Get all tickers that have credibility records."""
        try:
            result = (
                supabase.table("credibility_records")
                .select("ticker")
                .execute()
            )
            tickers = list(set(r["ticker"] for r in (result.data or [])))
            return sorted(tickers)
        except Exception as e:
            print(f"[credibility] Failed to get tickers: {e}")
            return []

    # ------------------------------------------------------------------
    # SUPABASE READ / WRITE
    # ------------------------------------------------------------------

    def _save_record(
        self,
        ticker: str,
        year: int,
        quarter: int,
        period: str,
        date: str,
        guidance_given: dict,
        actuals: dict,
        accuracy_scores: list,
        overall_accuracy: Optional[str],
    ):
        try:
            record = {
                "ticker": ticker,
                "year": year,
                "quarter": quarter,
                "period": period,
                "date": date,
                "guidance_given": json.dumps(guidance_given),
                "actuals": json.dumps(actuals),
                "accuracy_scores": json.dumps(accuracy_scores),
                "overall_accuracy": overall_accuracy,
            }
            supabase.table("credibility_records").upsert(record).execute()
            print(f"[credibility] Saved record for {ticker} {period}")
        except Exception as e:
            print(f"[credibility] Failed to save record: {e}")

    def _get_prior_record(self, ticker: str, year: int, quarter: int) -> Optional[dict]:
        """Get the most recent record before the current quarter."""
        try:
            prior_quarter = quarter - 1 if quarter > 1 else 4
            prior_year = year if quarter > 1 else year - 1

            result = (
                supabase.table("credibility_records")
                .select("*")
                .eq("ticker", ticker)
                .eq("year", prior_year)
                .eq("quarter", prior_quarter)
                .execute()
            )
            data = result.data or []
            return data[0] if data else None
        except Exception as e:
            print(f"[credibility] Failed to get prior record: {e}")
            return None

    def _update_accuracy(
        self,
        record_id: int,
        accuracy_scores: list,
        overall_accuracy: str,
    ):
        try:
            supabase.table("credibility_records").update({
                "accuracy_scores": json.dumps(accuracy_scores),
                "overall_accuracy": overall_accuracy,
            }).eq("id", record_id).execute()
        except Exception as e:
            print(f"[credibility] Failed to update accuracy: {e}")

    # ------------------------------------------------------------------
    # EXTRACT GUIDANCE AND ACTUALS
    # ------------------------------------------------------------------

    def _extract_guidance_given(self, guidance: dict) -> dict:
        nq = guidance.get("next_quarter_guidance", {})
        return {
            "revenue": nq.get("revenue"),
            "gross_margin": nq.get("gross_margin"),
            "operating_expenses": nq.get("operating_expenses"),
            "eps": nq.get("eps"),
            "guidance_tone": guidance.get("guidance_tone", ""),
            "notable_items": guidance.get("notable_guidance_items", []),
        }

    def _extract_actuals(self, kpis: dict) -> dict:
        return {
            "revenue": kpis.get("revenue", {}).get("reported"),
            "gross_margin": kpis.get("gross_margin", {}).get("reported"),
            "eps": kpis.get("eps", {}).get("reported"),
            "vs_guidance": kpis.get("revenue", {}).get("vs_guidance"),
        }

    # ------------------------------------------------------------------
    # SCORING
    # ------------------------------------------------------------------

    def _score_guidance(
        self,
        guidance_given: dict,
        actuals: dict,
        guidance_period: str,
        actual_period: str,
    ) -> list:
        scores = []

        if guidance_given.get("revenue") and actuals.get("revenue"):
            scores.append(self._assess_metric(
                metric="Revenue",
                guided=guidance_given["revenue"],
                actual=actuals["revenue"],
                vs_guidance=actuals.get("vs_guidance", "")
            ))

        if guidance_given.get("gross_margin") and actuals.get("gross_margin"):
            scores.append(self._assess_metric(
                metric="Gross Margin",
                guided=guidance_given["gross_margin"],
                actual=actuals["gross_margin"],
            ))

        if guidance_given.get("eps") and actuals.get("eps"):
            scores.append(self._assess_metric(
                metric="EPS",
                guided=guidance_given["eps"],
                actual=actuals["eps"],
            ))

        return scores

    def _numeric_compare(self, metric: str, guided_str: str, actual_str: str) -> Optional[dict]:
        """
        Deterministically compares a guided figure to an actual reported
        figure using real parsed numbers -- no AI call involved.

        Same two inputs will always produce the same output, and that
        output can be shown to anyone as a formula instead of a trust-me
        claim about an AI's phrasing. Returns None if either figure
        can't be parsed into a number, so the caller knows to fall back
        rather than silently guessing.
        """
        guided = parse_financial_value(guided_str)
        actual = parse_financial_value(actual_str)

        if guided is None or actual is None:
            return None

        if guided.is_range:
            # Companies often guide a RANGE (e.g. "$21.5-22.0B") because
            # the exact outcome isn't knowable in advance. Above the top
            # of the range = beat (under-promised, over-delivered).
            # Below the bottom = miss (over-promised). Inside the range
            # = in-line, they did what they said.
            if actual.midpoint > guided.high:
                result = "beat"
            elif actual.midpoint < guided.low:
                result = "miss"
            else:
                result = "in-line"
        else:
            # A single point estimate needs a tolerance band, since no
            # company hits one number exactly. Materially above the
            # band is a beat, materially below is a miss, small
            # deviations either way are in-line.
            tolerance = POINT_ESTIMATE_TOLERANCE.get(metric, DEFAULT_TOLERANCE)
            pct_diff = (
                (actual.midpoint - guided.midpoint) / abs(guided.midpoint)
                if guided.midpoint != 0 else 0.0
            )
            if pct_diff > tolerance:
                result = "beat"
            elif pct_diff < -tolerance:
                result = "miss"
            else:
                result = "in-line"

        delta = actual.midpoint - guided.midpoint
        return {
            "result": result,
            "guided_parsed": (
                f"{guided.low:,.2f}-{guided.high:,.2f}" if guided.is_range else f"{guided.low:,.2f}"
            ),
            "actual_parsed": f"{actual.midpoint:,.2f}",
            "delta": round(delta, 4),
        }

    def _assess_metric(
        self,
        metric: str,
        guided: str,
        actual: str,
        vs_guidance: str = "",
    ) -> dict:
        """
        Determines if guidance was a beat, miss, or in-line for one
        metric. Tries the numeric comparison FIRST -- real math on real
        parsed numbers. Only if that fails (guidance was qualitative
        text with no clean number, e.g. "double-digit growth") does
        this fall back to reading Claude's own wording, tagged clearly
        as lower confidence so it's never mistaken for a verified result.
        """
        numeric = self._numeric_compare(metric, guided, actual)
        if numeric is not None:
            return {
                "metric": metric,
                "guided": guided,
                "actual": actual,
                "result": numeric["result"],
                "confidence": "high",
                "method": "numeric",
                "note": (
                    f"Guided {numeric['guided_parsed']} -> Actual {numeric['actual_parsed']} "
                    f"(delta {numeric['delta']:+,.2f})"
                ),
            }

        vs = vs_guidance.lower() if vs_guidance else ""
        if "beat" in vs:
            result = "beat"
        elif "miss" in vs or "missed" in vs:
            result = "miss"
        elif "in-line" in vs or "inline" in vs or "in line" in vs:
            result = "in-line"
        else:
            result = "unscored"

        return {
            "metric": metric,
            "guided": guided,
            "actual": actual,
            "result": result,
            "confidence": "low",
            "method": "ai_wording_fallback",
            "note": (
                f"Could not parse numeric values from '{guided}' / '{actual}'; "
                f"falling back to AI's own assessment: {vs_guidance or 'none given'}"
            ),
        }

    def _compute_overall(self, scores: list) -> str:
        # "unscored" means neither numeric parsing nor the AI-wording
        # fallback could determine a result -- there's no signal here,
        # so it must be excluded rather than silently counted.
        scored = [s for s in scores if s["result"] != "unscored"]
        if not scored:
            return "insufficient_data"
        beats = sum(1 for s in scored if s["result"] == "beat")
        misses = sum(1 for s in scored if s["result"] == "miss")
        if beats > misses:
            return "beat"
        elif misses > beats:
            return "miss"
        return "in-line"

    def _compute_credibility_score(self, history: dict) -> dict:
        records = history.get("records", [])
        scored = [
            r for r in records
            if r.get("overall_accuracy") and r["overall_accuracy"] != "insufficient_data"
        ]

        if not scored:
            return {
                "score": None,
                "label": "Insufficient Data",
                "beats": 0,
                "misses": 0,
                "in_line": 0,
                "total": 0,
                "numeric_verification_rate": None,
            }

        beats   = sum(1 for r in scored if r["overall_accuracy"] == "beat")
        misses  = sum(1 for r in scored if r["overall_accuracy"] == "miss")
        in_line = sum(1 for r in scored if r["overall_accuracy"] == "in-line")
        total   = len(scored)
        score   = round((beats + (in_line * 0.5)) / total * 100)

        if score >= 75:
            label = "High Credibility"
        elif score >= 50:
            label = "Moderate Credibility"
        else:
            label = "Low Credibility"

        # What fraction of the underlying metric-level scores were
        # numerically verified vs. AI-wording fallback -- the number
        # you show a skeptical client who asks "how do I know this
        # score is real."
        all_metric_scores = [s for r in records for s in (r.get("accuracy_scores") or [])]
        numeric_count = sum(1 for s in all_metric_scores if s.get("method") == "numeric")
        verification_rate = (
            round(numeric_count / len(all_metric_scores) * 100) if all_metric_scores else None
        )

        return {
            "score": score,
            "label": label,
            "beats": beats,
            "misses": misses,
            "in_line": in_line,
            "total": total,
            "numeric_verification_rate": verification_rate,
        }

    # ------------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------------

    def _parse_period(self, period: str) -> tuple:
        """Parse 'Q3 2024' into (2024, 3)."""
        try:
            parts = period.strip().split()
            quarter = int(parts[0].replace("Q", ""))
            year = int(parts[1])
            return year, quarter
        except Exception:
            return datetime.now().year, 1
        