"""
server.py
---------
FastAPI backend. Serves the UI and exposes an API endpoint
that streams analysis progress back to the browser in real time.
"""

import sys
import json
import asyncio
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
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


@app.post("/analyze")
async def analyze(req: AnalyzeRequest):
    """
    Streams analysis results back to the browser as server-sent events.
    Each step sends a JSON chunk as it completes.
    """
    async def event_stream():
        try:
            # Add project root to path
            project_root = Path(__file__).parent.parent
            if str(project_root) not in sys.path:
                sys.path.insert(0, str(project_root))

            from backend.data.transcript_fetcher import TranscriptFetcher
            from backend.agents.earnings_agent import EarningsAgent
            from backend.agents.memo_generator import MemoGenerator

            def send(event: str, data: dict):
                return f"data: {json.dumps({'event': event, 'payload': data})}\n\n"

            # Step 1 — Fetch transcript
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

            # Step 2 — Fetch prior quarter
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

            # Step 3 — Run analysis (each sub-step streams back)
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

            # Step 4 — Delta summary
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

            # Step 5 — Build final analysis and memo
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

            memo_gen = MemoGenerator()
            result = memo_gen.generate(analysis)

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
