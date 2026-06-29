"""
server.py
---------
FastAPI backend. Serves the UI and exposes API endpoints
that stream analysis progress and serve the credibility tracker.
"""

import os
import sys
import json
import asyncio
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


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
    project_root = Path(__file__).parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from backend.agents.credibility_tracker import CredibilityTracker
    tracker = CredibilityTracker()
    history = tracker.get_history(ticker.upper())
    return JSONResponse(content=history)


@app.get("/api/credibility")
async def get_all_credibility():
    project_root = Path(__file__).parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
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
    async def event_stream():
        try:
            project_root = Path(__file__).parent
            if str(project_root) not in sys.path:
                sys.path.insert(0, str(project_root))

            from backend.data.transcript_fetcher import TranscriptFetcher
            from backend.agents.earnings_agent import EarningsAgent
            from backend.agents.memo_generator import MemoGenerator
            from backend.agents.credibility_tracker import CredibilityTracker

            def send(event: str, data: dict):
                return f"data: {json.dumps({'event': event, 'payload': data})}\n\n"

            yield send("status", {"message": f"Fetching {req.ticker} Q{req.quarter} {req.year} transcript..."})
            await asyncio.sleep(0.1)

            fetcher = TranscriptFetcher()
            transcript = await asyncio.get_event_loop().run_in_executor(
                None, lambda: fetcher.get_transcript(req.ticker, req.year, req.quarter)
            )

            if not transcript:
                yield send("error", {"message": f"Could not find transcript for {req.ticker} Q{req.quarter} {req.year}. Try a different ticker or quarter."})
                return

            yield send("status", {"message": f"Transcript loaded ({len(transcript['content'])} characters). Fetching prior quarter..."})
            await asyncio.sleep(0.1)

            prior_quarter = req.quarter - 1 if req.quarter > 1 else 4
            prior_year = req.year if req.quarter > 1 else req.year - 1
            prior_transcript = await asyncio.get_event_loop().run_in_executor(
                None, lambda: fetcher.get_transcript(req.ticker, prior_year, prior_quarter)
            )

            if prior_transcript:
                yield send("status", {"message": f"Prior quarter loaded. Starting AI analysis..."})
            else:
                yield send("status", {"message": f"Prior quarter not found. Running single-quarter analysis..."})
            await asyncio.sleep(0.1)

            agent = EarningsAgent()

            yield send("step", {"step": 1, "label": "Extracting KPIs", "status": "running"})
            await asyncio.sleep(0.1)
            kpis = await asyncio.get_event_loop().run_in_executor(
                None, lambda: agent._extract_kpis(transcript["content"], req.ticker)
            )
            yield send("step", {"step": 1, "label": "KPIs extracted", "status": "done", "data": kpis})

            yield send("step", {"step": 2, "label": "Analyzing management tone", "status": "running"})
            await asyncio.sleep(0.1)
            tone = await asyncio.get_event_loop().run_in_executor(
                None, lambda: agent._analyze_tone(transcript["content"], req.ticker)
            )
            yield send("step", {"step": 2, "label": "Tone analyzed", "status": "done", "data": tone})

            yield send("step", {"step": 3, "label": "Extracting guidance", "status": "running"})
            await asyncio.sleep(0.1)
            prior_content = prior_transcript["content"] if prior_transcript else None
            guidance = await asyncio.get_event_loop().run_in_executor(
                None, lambda: agent._extract_guidance(transcript["content"], req.ticker, prior_content)
            )
            yield send("step", {"step": 3, "label": "Guidance extracted", "status": "done", "data": guidance})

            yield send("step", {"step": 4, "label": "Detecting narrative shifts", "status": "running"})
            await asyncio.sleep(0.1)
            narrative = await asyncio.get_event_loop().run_in_executor(
                None, lambda: agent._detect_narrative_shifts(transcript["content"], req.ticker, prior_content)
            )
            yield send("step", {"step": 4, "label": "Narrative shifts detected", "status": "done", "data": narrative})

            yield send("step", {"step": 5, "label": "Analyzing Q&A intelligence", "status": "running"})
            await asyncio.sleep(0.1)
            qa_section = transcript.get("sections", {}).get("qa", transcript["content"])
            qa_intel = await asyncio.get_event_loop().run_in_executor(
                None, lambda: agent._analyze_qa(qa_section)
            )
            yield send("step", {"step": 5, "label": "Q&A analyzed", "status": "done", "data": qa_intel})

            delta_summary = ""
            if prior_transcript:
                yield send("status", {"message": "Generating quarter-over-quarter delta..."})
                await asyncio.sleep(0.1)
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

            # Record to credibility tracker
            yield send("status", {"message": "Recording to credibility tracker..."})
            tracker = CredibilityTracker()
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: tracker.record(req.ticker, analysis)
            )

            yield send("status", {"message": "Generating memo..."})
            await asyncio.sleep(0.1)

            memo_gen = MemoGenerator()
            result = memo_gen.generate(analysis)

            # Send keepalive pings before final payload
            # to prevent Railway from dropping the connection
            for _ in range(3):
                yield ": keepalive\n\n"
                await asyncio.sleep(0.1)

            yield send("complete", {
                "memo_text": result["memo_text"],
                "memo_path": result["memo_path"],
                "signal": overall_signal,
                "ticker": req.ticker,
                "period": f"Q{req.quarter} {req.year}",
                "analysis": analysis,
            })

        except Exception as e:
            yield f"data: {json.dumps({'event': 'error', 'payload': {'message': str(e)}})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)