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

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


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

    def _assess_metric(
        self,
        metric: str,
        guided: str,
        actual: str,
        vs_guidance: str = "",
    ) -> dict:
        result = "in-line"
        confidence = "low"
        note = ""

        vs = vs_guidance.lower() if vs_guidance else ""

        if "beat" in vs:
            result = "beat"
            confidence = "high"
            note = vs_guidance
        elif "miss" in vs or "missed" in vs:
            result = "miss"
            confidence = "high"
            note = vs_guidance
        elif "in-line" in vs or "inline" in vs or "in line" in vs:
            result = "in-line"
            confidence = "high"
            note = vs_guidance
        else:
            note = f"Guided: {guided} → Actual: {actual}"
            confidence = "low"

        return {
            "metric": metric,
            "guided": guided,
            "actual": actual,
            "result": result,
            "confidence": confidence,
            "note": note,
        }

    def _compute_overall(self, scores: list) -> str:
        if not scores:
            return "insufficient_data"
        beats = sum(1 for s in scores if s["result"] == "beat")
        misses = sum(1 for s in scores if s["result"] == "miss")
        if beats > misses:
            return "beat"
        elif misses > beats:
            return "miss"
        return "in-line"

    def _compute_credibility_score(self, history: dict) -> dict:
        records = history.get("records", [])
        scored = [r for r in records if r.get("overall_accuracy")]

        if not scored:
            return {
                "score": None,
                "label": "Insufficient Data",
                "beats": 0,
                "misses": 0,
                "in_line": 0,
                "total": 0,
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

        return {
            "score": score,
            "label": label,
            "beats": beats,
            "misses": misses,
            "in_line": in_line,
            "total": total,
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
        