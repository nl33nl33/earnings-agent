"""
credibility_tracker.py
----------------------
Tracks management guidance accuracy over time.
Every time a company reports earnings, we record what
they guided for last quarter and whether they hit it.

Stores data in data/credibility/ as JSON files.
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Optional

CREDIBILITY_DIR = Path(__file__).parent.parent.parent / "data" / "credibility"
CREDIBILITY_DIR.mkdir(parents=True, exist_ok=True)


class CredibilityTracker:

    def record(self, ticker: str, analysis: dict):
        """
        Called after every analysis. Records:
        1. What management guided for THIS quarter (saves for future comparison)
        2. How accurate LAST quarter's guidance was vs what was actually reported
        """
        ticker = ticker.upper()
        period = analysis.get("period", "")
        date = analysis.get("date", datetime.now().strftime("%Y-%m-%d"))
        guidance = analysis.get("guidance", {})
        kpis = analysis.get("kpis", {})

        # Load existing history
        history = self._load_history(ticker)

        # Build a record for this quarter
        record = {
            "period": period,
            "date": date,
            "guidance_given": self._extract_guidance_given(guidance),
            "actuals": self._extract_actuals(kpis),
            "accuracy_scores": [],
            "overall_accuracy": None,
        }

        # Score prior quarter's guidance against this quarter's actuals
        if history.get("records"):
            last_record = history["records"][-1]
            scores = self._score_guidance(
                last_record.get("guidance_given", {}),
                record["actuals"],
                last_record["period"],
                period
            )
            last_record["accuracy_scores"] = scores
            last_record["overall_accuracy"] = self._compute_overall(scores)

        # Add new record
        history["records"].append(record)
        history["ticker"] = ticker
        history["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        history["overall_credibility"] = self._compute_credibility_score(history)

        self._save_history(ticker, history)
        return history

    def get_history(self, ticker: str) -> dict:
        return self._load_history(ticker.upper())

    def get_all_tickers(self) -> list:
        files = CREDIBILITY_DIR.glob("*.json")
        return sorted([f.stem for f in files])

    # ------------------------------------------------------------------
    # EXTRACT GUIDANCE GIVEN THIS QUARTER
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

    # ------------------------------------------------------------------
    # EXTRACT ACTUALS REPORTED THIS QUARTER
    # ------------------------------------------------------------------

    def _extract_actuals(self, kpis: dict) -> dict:
        return {
            "revenue": kpis.get("revenue", {}).get("reported"),
            "gross_margin": kpis.get("gross_margin", {}).get("reported"),
            "eps": kpis.get("eps", {}).get("reported"),
            "vs_guidance": kpis.get("revenue", {}).get("vs_guidance"),
        }

    # ------------------------------------------------------------------
    # SCORE PRIOR GUIDANCE VS ACTUAL RESULTS
    # ------------------------------------------------------------------

    def _score_guidance(
        self,
        guidance_given: dict,
        actuals: dict,
        guidance_period: str,
        actual_period: str
    ) -> list:
        scores = []

        # Revenue guidance vs actual
        if guidance_given.get("revenue") and actuals.get("revenue"):
            score = self._assess_metric(
                metric="Revenue",
                guided=guidance_given["revenue"],
                actual=actuals["revenue"],
                vs_guidance=actuals.get("vs_guidance", "")
            )
            scores.append(score)

        # Gross margin guidance vs actual
        if guidance_given.get("gross_margin") and actuals.get("gross_margin"):
            score = self._assess_metric(
                metric="Gross Margin",
                guided=guidance_given["gross_margin"],
                actual=actuals["gross_margin"],
            )
            scores.append(score)

        # EPS guidance vs actual
        if guidance_given.get("eps") and actuals.get("eps"):
            score = self._assess_metric(
                metric="EPS",
                guided=guidance_given["eps"],
                actual=actuals["eps"],
            )
            scores.append(score)

        return scores

    def _assess_metric(
        self,
        metric: str,
        guided: str,
        actual: str,
        vs_guidance: str = ""
    ) -> dict:
        """
        Determines if guidance was a beat, miss, or in-line.
        Uses the vs_guidance field from KPI extraction when available,
        otherwise makes a qualitative assessment.
        """
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
            # Qualitative comparison
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

        score = round((beats + (in_line * 0.5)) / total * 100)

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
    # STORAGE
    # ------------------------------------------------------------------

    def _load_history(self, ticker: str) -> dict:
        path = CREDIBILITY_DIR / f"{ticker}.json"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        return {"ticker": ticker, "records": [], "overall_credibility": None}

    def _save_history(self, ticker: str, history: dict):
        path = CREDIBILITY_DIR / f"{ticker}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
