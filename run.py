"""
run.py
------
The main entry point for the Earnings Research Agent.
Run this file to analyze an earnings call.

Usage:
    python run.py
"""

import sys
import json
from pathlib import Path

# Make sure Python can find our backend folder
sys.path.insert(0, str(Path(__file__).parent))

from backend.data.transcript_fetcher import get_demo_transcript, TranscriptFetcher
from backend.agents.earnings_agent import EarningsAgent
from backend.agents.memo_generator import MemoGenerator


def run_demo():
    """
    Runs the agent on the built-in NVIDIA demo transcript.
    Works with zero API keys for the transcript (only needs Anthropic).
    """
    print("\n" + "=" * 60)
    print("  EARNINGS RESEARCH AGENT")
    print("  Mode: Demo (NVIDIA Q4 FY2024)")
    print("=" * 60)

    # Step 1 — Load the demo transcript
    print("\n[Loading transcript...]")
    transcript = get_demo_transcript()
    print(f"[✓] Loaded: {transcript['title']}")
    print(f"    Length: {len(transcript['content'])} characters")

    # Step 2 — Run the AI analysis
    print("\n[Starting AI analysis — this takes about 30-60 seconds...]")
    agent = EarningsAgent()
    analysis = agent.analyze(transcript)

    # Step 3 — Generate the memo
    print("\n[Generating memo...]")
    memo_gen = MemoGenerator()
    result = memo_gen.generate(analysis)

    # Step 4 — Print the memo to the terminal
    print("\n\n")
    print(result["memo_text"])

    print(f"\n[✓] Files saved to:")
    print(f"    Memo:     {result['memo_path']}")
    print(f"    Raw JSON: {result['json_path']}")

    return result


def run_custom(ticker: str, year: int, quarter: int):
    """
    Runs the agent on a real ticker using the FMP API.
    Requires FMP_API_KEY in your .env file.
    """
    print("\n" + "=" * 60)
    print(f"  EARNINGS RESEARCH AGENT")
    print(f"  Mode: Live — {ticker} Q{quarter} {year}")
    print("=" * 60)

    fetcher = TranscriptFetcher()

    # Step 1 — Fetch current quarter transcript
    print(f"\n[Fetching {ticker} Q{quarter} {year} transcript...]")
    transcript = fetcher.get_transcript(ticker, year, quarter)

    if not transcript:
        print(f"\n[!] Could not fetch transcript for {ticker} Q{quarter} {year}")
        print("    Make sure your FMP_API_KEY is set in .env")
        print("    Get a free key at: https://financialmodelingprep.com")
        return None

    print(f"[✓] Loaded: {transcript['title']}")

    # Step 2 — Try to fetch prior quarter for comparison
    prior_quarter = quarter - 1 if quarter > 1 else 4
    prior_year = year if quarter > 1 else year - 1

    print(f"\n[Fetching prior quarter ({ticker} Q{prior_quarter} {prior_year}) for comparison...]")
    prior_transcript = fetcher.get_transcript(ticker, prior_year, prior_quarter)

    if prior_transcript:
        print(f"[✓] Prior quarter loaded for delta comparison")
    else:
        print(f"[!] Prior quarter not found — skipping delta comparison")

    # Step 3 — Run the AI analysis
    print("\n[Starting AI analysis — this takes about 30-60 seconds...]")
    agent = EarningsAgent()
    analysis = agent.analyze(transcript, prior_transcript=prior_transcript)

    # Step 4 — Generate the memo
    print("\n[Generating memo...]")
    memo_gen = MemoGenerator()
    result = memo_gen.generate(analysis)

    # Step 5 — Print the memo
    print("\n\n")
    print(result["memo_text"])

    print(f"\n[✓] Files saved to:")
    print(f"    Memo:     {result['memo_path']}")
    print(f"    Raw JSON: {result['json_path']}")

    return result


# ------------------------------------------------------------------
# MAIN — edit this section to choose what to run
# ------------------------------------------------------------------

if __name__ == "__main__":

    # OPTION A — Demo mode
    # run_demo()

    # OPTION B — Real ticker
    run_custom(
    ticker="NVDA",
    year=2024,
    quarter=3
)
    
