"""
server.py
---------
FastAPI backend. Uses a simple POST request instead of streaming
for better reliability on Railway.
"""

import os
import sys
import json
import asyncio
import time
from pathlib import Path
from collections import defaultdict
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Add project root to path once at startup
project_root = Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from backend.database import save_analysis, get_credibility_claims, get_credibility_score


# ---------------------------------------------------------------------------
# RATE LIMITING + SPEND CAP
# ---------------------------------------------------------------------------

RATE_LIMIT_REQUESTS = 5
RATE_LIMIT_WINDOW   = 3600
DAILY_REQUEST_CAP   = 50

_ip_requests: dict = defaultdict(list)
_daily_count: dict = {"date": "", "count": 0}


def _get_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host


def _check_rate_limit(ip: str) -> tuple[bool, str]:
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW
    _ip_requests[ip] = [t for t in _ip_requests[ip] if t > window_start]
    if len(_ip_requests[ip]) >= RATE_LIMIT_REQUESTS:
        return False, f"Rate limit reached. You can run {RATE_LIMIT_REQUESTS} analyses per hour. Please try again later."
    _ip_requests[ip].append(now)
    return True, ""


def _check_daily_cap() -> tuple[bool, str]:
    today = time.strftime("%Y-%m-%d")
    if _daily_count["date"] != today:
        _daily_count["date"] = today
        _daily_count["count"] = 0
    if _daily_count["count"] >= DAILY_REQUEST_CAP:
        return False, "Daily analysis limit reached. The platform resets at midnight. Please check back tomorrow."
    _daily_count["count"] += 1
    return True, ""


# ---------------------------------------------------------------------------
# MODELS
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    ticker: str
    year: int
    quarter: int


# ---------------------------------------------------------------------------
# ROUTES
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    html_path = Path(__file__).parent / "index.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.get("/credibility", response_class=HTMLResponse)
async def serve_credibility():
    html_path = Path(__file__).parent / "credibility.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"))


@app.get("/api/credibility/{ticker}")
async def get_credibility(ticker: str):
    from backend.agents.credibility_tracker import CredibilityTracker
    tracker = CredibilityTracker()
    history = tracker.get_history(ticker.upper())
    score = get_credibility_score(ticker.upper())
    history["supabase_score"] = score
    return JSONResponse(content=history)


@app.get("/api/credibility")
async def get_all_credibility():
    from backend.agents.credibility_tracker import CredibilityTracker
    tracker = CredibilityTracker()
    tickers = tracker.get_all_tickers()
    all_data = []
    for ticker in tickers:
        history = tracker.get_history(ticker)
        all_data.append({
            "ticker": ticker,
            "overall_credibility": history.get("overall_credibility"),
            "last_updated": history.get("last_updated"),
            "quarters_tracked": len(history.get("records", [])),
        })
    return JSONResponse(content=all_data)


@app.get("/api/status")
async def get_status():
    today = time.strftime("%Y-%m-%d")
    daily_used = _daily_count["count"] if _daily_count["date"] == today else 0
    return JSONResponse(content={
        "daily_analyses_used": daily_used,
        "daily_analyses_cap": DAILY_REQUEST_CAP,
        "daily_analyses_remaining": max(0, DAILY_REQUEST_CAP - daily_used),
        "rate_limit_per_hour": RATE_LIMIT_REQUESTS,
    })


@app.get("/api/recent-analyses")
async def get_recent_analyses():
    try:
        from backend.database import supabase
        result = (
            supabase.table("analyses")
            .select("ticker, year, quarter, signal, analysis_date")
            .order("analysis_date", desc=True)
            .limit(8)
            .execute()
        )
        return JSONResponse(content=result.data or [])
    except Exception:
        return JSONResponse(content=[])


@app.post("/analyze")
async def analyze(req: AnalyzeRequest, request: Request):
    cap_ok, cap_msg = _check_daily_cap()
    if not cap_ok:
        return JSONResponse(status_code=429, content={"error": cap_msg})

    ip = _get_ip(request)
    rate_ok, rate_msg = _check_rate_limit(ip)
    if not rate_ok:
        return JSONResponse(status_code=429, content={"error": rate_msg})

    try:
        from backend.data.transcript_fetcher import TranscriptFetcher
        from backend.agents.earnings_agent import EarningsAgent
        from backend.agents.memo_generator import MemoGenerator
        from backend.agents.credibility_tracker import CredibilityTracker

        fetcher = TranscriptFetcher()
        transcript = await asyncio.get_event_loop().run_in_executor(
            None, lambda: fetcher.get_transcript(req.ticker, req.year, req.quarter)
        )

        if not transcript:
            return JSONResponse(
                status_code=404,
                content={"error": f"No earnings transcript found for {req.ticker} Q{req.quarter} {req.year}. The company may not have filed with the SEC for this period, or the transcript isn't available yet."}
            )

        prior_quarter = req.quarter - 1 if req.quarter > 1 else 4
        prior_year = req.year if req.quarter > 1 else req.year - 1
        prior_transcript = await asyncio.get_event_loop().run_in_executor(
            None, lambda: fetcher.get_transcript(req.ticker, prior_year, prior_quarter)
        )

        agent = EarningsAgent()
        prior_content = prior_transcript["content"] if prior_transcript else None

        kpis = await asyncio.get_event_loop().run_in_executor(
            None, lambda: agent._extract_kpis(transcript["content"], req.ticker)
        )
        tone = await asyncio.get_event_loop().run_in_executor(
            None, lambda: agent._analyze_tone(transcript["content"], req.ticker)
        )
        guidance = await asyncio.get_event_loop().run_in_executor(
            None, lambda: agent._extract_guidance(transcript["content"], req.ticker, prior_content)
        )
        narrative = await asyncio.get_event_loop().run_in_executor(
            None, lambda: agent._detect_narrative_shifts(transcript["content"], req.ticker, prior_content)
        )
        qa_section = transcript.get("sections", {}).get("qa", transcript["content"])
        qa_intel = await asyncio.get_event_loop().run_in_executor(
            None, lambda: agent._analyze_qa(qa_section)
        )

        delta_summary = ""
        if prior_transcript:
            delta_summary = await asyncio.get_event_loop().run_in_executor(
                None, lambda: agent._generate_delta_summary(
                    kpis, tone, guidance, narrative,
                    req.ticker,
                    f"Q{req.quarter} {req.year}",
                    f"Q{prior_quarter} {prior_year}"
                )
            )

        overall_signal = agent._determine_signal(kpis, tone, guidance)
        analysis = {
            "ticker": req.ticker,
            "period": f"Q{req.quarter} {req.year}",
            "date": transcript.get("date", ""),
            "kpis": kpis,
            "tone": tone,
            "guidance": guidance,
            "narrative": narrative,
            "qa_intelligence": qa_intel,
            "overall_signal": overall_signal,
            "delta_summary": delta_summary,
            "has_prior_comparison": prior_transcript is not None,
        }

        await asyncio.get_event_loop().run_in_executor(
            None, lambda: save_analysis(req.ticker, req.year, req.quarter, analysis)
        )

        tracker = CredibilityTracker()
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: tracker.record(req.ticker, analysis)
        )

        memo_gen = MemoGenerator()
        result = memo_gen.generate(analysis)

        return JSONResponse(content={
            "memo_text": result["memo_text"],
            "memo_path": result["memo_path"],
            "signal": overall_signal,
            "ticker": req.ticker,
            "period": f"Q{req.quarter} {req.year}",
            "analysis": analysis,
        })

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
