"""
database.py
-----------
Supabase database connection and helper functions.
Handles all reads and writes for analyses and credibility claims.
"""

import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY in environment variables")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ---------------------------------------------------------------------------
# ANALYSES
# ---------------------------------------------------------------------------

def save_analysis(ticker: str, year: int, quarter: int, analysis: dict) -> bool:
    """Save a completed analysis to Supabase. Overwrites if already exists."""
    try:
        kpis = analysis.get("kpis", {})
        tone = analysis.get("tone", {})
        guidance = analysis.get("guidance", {})

        record = {
            "ticker": ticker.upper(),
            "year": year,
            "quarter": quarter,
            "signal": analysis.get("signal"),
            "confidence_score": tone.get("confidence_score"),
            "revenue": str(kpis.get("revenue", "")),
            "revenue_growth": str(kpis.get("revenue_growth_yoy", "")),
            "gross_margin": str(kpis.get("gross_margin", "")),
            "eps": str(kpis.get("eps", "")),
            "guidance_next_quarter": str(guidance.get("next_quarter", "")),
            "tone_ceo": tone.get("ceo_tone"),
            "tone_cfo": tone.get("cfo_tone"),
            "narrative_shifts": analysis.get("narrative_shifts"),
            "red_flags": tone.get("red_flags"),
            "full_analysis": analysis,
        }

        supabase.table("analyses").upsert(record).execute()
        print(f"[db] Saved analysis for {ticker} Q{quarter} {year}")
        return True

    except Exception as e:
        print(f"[db] Failed to save analysis: {e}")
        return False


def get_analysis(ticker: str, year: int, quarter: int) -> dict | None:
    """Retrieve a saved analysis from Supabase."""
    try:
        result = (
            supabase.table("analyses")
            .select("*")
            .eq("ticker", ticker.upper())
            .eq("year", year)
            .eq("quarter", quarter)
            .single()
            .execute()
        )
        return result.data
    except Exception:
        return None


def get_company_history(ticker: str) -> list:
    """Get all analyses for a company, ordered by most recent first."""
    try:
        result = (
            supabase.table("analyses")
            .select("ticker, year, quarter, signal, confidence_score, revenue, revenue_growth, gross_margin, analysis_date")
            .eq("ticker", ticker.upper())
            .order("year", desc=True)
            .order("quarter", desc=True)
            .execute()
        )
        return result.data or []
    except Exception as e:
        print(f"[db] Failed to get history: {e}")
        return []


# ---------------------------------------------------------------------------
# CREDIBILITY CLAIMS
# ---------------------------------------------------------------------------

def save_credibility_claim(
    ticker: str,
    year: int,
    quarter: int,
    claim_text: str,
    claim_type: str = "guidance",
) -> bool:
    """Save a new management claim to track."""
    try:
        record = {
            "ticker": ticker.upper(),
            "year": year,
            "quarter": quarter,
            "claim_text": claim_text,
            "claim_type": claim_type,
            "status": "pending",
        }
        supabase.table("credibility_claims").insert(record).execute()
        print(f"[db] Saved credibility claim for {ticker}")
        return True
    except Exception as e:
        print(f"[db] Failed to save claim: {e}")
        return False


def get_credibility_claims(ticker: str) -> list:
    """Get all tracked claims for a company."""
    try:
        result = (
            supabase.table("credibility_claims")
            .select("*")
            .eq("ticker", ticker.upper())
            .order("year", desc=True)
            .order("quarter", desc=True)
            .execute()
        )
        return result.data or []
    except Exception as e:
        print(f"[db] Failed to get claims: {e}")
        return []


def update_claim_status(
    claim_id: int,
    status: str,
    notes: str = "",
    verified_year: int = None,
    verified_quarter: int = None,
) -> bool:
    """Update whether a management claim was accurate or not."""
    try:
        update = {"status": status, "notes": notes}
        if verified_year:
            update["verified_year"] = verified_year
        if verified_quarter:
            update["verified_quarter"] = verified_quarter

        supabase.table("credibility_claims").update(update).eq("id", claim_id).execute()
        return True
    except Exception as e:
        print(f"[db] Failed to update claim: {e}")
        return False


def get_credibility_score(ticker: str) -> dict:
    """Calculate a company's credibility score based on verified claims."""
    try:
        result = (
            supabase.table("credibility_claims")
            .select("status")
            .eq("ticker", ticker.upper())
            .neq("status", "pending")
            .execute()
        )
        claims = result.data or []
        if not claims:
            return {"score": None, "total": 0, "accurate": 0, "inaccurate": 0}

        accurate = sum(1 for c in claims if c["status"] == "accurate")
        inaccurate = sum(1 for c in claims if c["status"] == "inaccurate")
        total = len(claims)
        score = round((accurate / total) * 100) if total > 0 else None

        return {
            "score": score,
            "total": total,
            "accurate": accurate,
            "inaccurate": inaccurate,
        }
    except Exception as e:
        print(f"[db] Failed to calculate score: {e}")
        return {"score": None, "total": 0, "accurate": 0, "inaccurate": 0}
