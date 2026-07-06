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
from pathlib import Path
from fastapi import FastAPI
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


class AnalyzeRequest(BaseModel):
    ticker: str
    year: int
    quarter: int


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

    # Also pull from Supabase and attach credibility score
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


@app.post("/analyze")
async def analyze(req: AnalyzeRequest):
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
                content={"error": f"Could not find transcript for {req.ticker} Q{req.quarter} {req.year}"}
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

        # Save to Supabase — persists across redeploys
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: save_analysis(req.ticker, req.year, req.quarter, analysis)
        )

        # Save to credibility tracker
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
