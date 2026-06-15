"""
earnings_agent.py
-----------------
The core AI agent. Sends the transcript to Claude
in 5 separate focused analyses, then combines everything.
"""

import os
import re
import json
import anthropic
from typing import Optional
from dotenv import load_dotenv

load_dotenv()


class EarningsAgent:

    MODEL = "claude-sonnet-4-5"
    MAX_TOKENS = 2000

    def __init__(self):
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if not api_key or api_key == "your_anthropic_key_here":
            raise ValueError(
                "\n\n[!] ANTHROPIC_API_KEY not set.\n"
                "    1. Open your .env file\n"
                "    2. Paste your key from console.anthropic.com\n"
            )
        self.client = anthropic.Anthropic(api_key=api_key)

    # ------------------------------------------------------------------
    # MAIN FUNCTION
    # ------------------------------------------------------------------

    def analyze(self, transcript: dict, prior_transcript: Optional[dict] = None) -> dict:
        ticker = transcript.get("ticker", "UNKNOWN")
        period = f"Q{transcript.get('quarter')} {transcript.get('year')}"
        content = transcript.get("content", "")

        print(f"\n{'='*60}")
        print(f"  Analyzing {ticker} {period}")
        print(f"{'='*60}")

        print("\n[1/5] Extracting KPIs...")
        kpis = self._extract_kpis(content, ticker)

        print("[2/5] Analyzing management tone...")
        tone = self._analyze_tone(content, ticker)

        print("[3/5] Extracting guidance...")
        guidance = self._extract_guidance(
            content, ticker,
            prior_content=prior_transcript.get("content", "") if prior_transcript else None
        )

        print("[4/5] Detecting narrative shifts...")
        narrative = self._detect_narrative_shifts(
            content, ticker,
            prior_content=prior_transcript.get("content", "") if prior_transcript else None
        )

        print("[5/5] Analyzing Q&A session...")
        qa_intel = self._analyze_qa(
            transcript.get("sections", {}).get("qa", content)
        )

        delta_summary = ""
        if prior_transcript:
            print("\n[+] Generating delta summary vs prior quarter...")
            delta_summary = self._generate_delta_summary(
                current_kpis=kpis,
                current_tone=tone,
                current_guidance=guidance,
                current_narrative=narrative,
                ticker=ticker,
                current_period=period,
                prior_period=f"Q{prior_transcript.get('quarter')} {prior_transcript.get('year')}",
            )

        overall_signal = self._determine_signal(kpis, tone, guidance)
        print(f"\n[✓] Analysis complete. Signal: {overall_signal.upper()}")

        return {
            "ticker": ticker,
            "period": period,
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

    # ------------------------------------------------------------------
    # STEP 1 — Extract all the numbers
    # ------------------------------------------------------------------

    def _extract_kpis(self, content: str, ticker: str) -> dict:
        prompt = f"""You are a senior equity research analyst at a top hedge fund.
Extract all quantitative KPIs from this earnings call transcript for {ticker}.

Return ONLY valid JSON in exactly this structure (no markdown, no explanation):
{{
  "revenue": {{
    "reported": "string value with units e.g. $22.1B",
    "yoy_growth": "e.g. +265%",
    "qoq_growth": "e.g. +22%",
    "vs_guidance": "beat/miss/in-line, by how much"
  }},
  "gross_margin": {{
    "reported": "e.g. 76.7%",
    "yoy_change": "e.g. +800bps",
    "trend": "expanding/contracting/stable"
  }},
  "operating_income": {{
    "reported": "string or null",
    "margin": "string or null"
  }},
  "eps": {{
    "reported": "string or null",
    "vs_consensus": "string or null"
  }},
  "segment_highlights": [
    {{"name": "segment name", "revenue": "value", "growth": "yoy %"}}
  ],
  "cash_and_balance_sheet": {{
    "cash": "string or null",
    "key_note": "any important balance sheet comment"
  }},
  "other_key_metrics": [
    {{"metric": "name", "value": "value", "context": "why it matters"}}
  ]
}}

TRANSCRIPT:
{content[:6000]}
"""
        return self._call_claude_json(prompt, "kpis")

    # ------------------------------------------------------------------
    # STEP 2 — Analyze how confident management sounds
    # ------------------------------------------------------------------

    def _analyze_tone(self, content: str, ticker: str) -> dict:
        prompt = f"""You are a behavioral finance expert analyzing management communication.
Analyze the tone and confidence of management in this {ticker} earnings call.

Return ONLY valid JSON (no markdown):
{{
  "overall_confidence_score": 8,
  "confidence_rationale": "2-3 sentence explanation",
  "ceo_tone": "confident",
  "cfo_tone": "confident",
  "ceo_cfo_divergence": false,
  "divergence_note": null,
  "hedging_phrases_detected": ["phrase one", "phrase two"],
  "strong_conviction_phrases": ["phrase one", "phrase two"],
  "sentiment_by_topic": {{
    "revenue_growth": "positive",
    "margins": "positive",
    "competition": "neutral",
    "demand_outlook": "positive"
  }},
  "red_flags": [],
  "standout_positive": ["statement one", "statement two"]
}}

Important: overall_confidence_score must be a number not a string.
Important: ceo_cfo_divergence must be true or false not a string.

TRANSCRIPT:
{content[:6000]}
"""
        return self._call_claude_json(prompt, "tone")

    # ------------------------------------------------------------------
    # STEP 3 — Pull out what management promised for next quarter
    # ------------------------------------------------------------------

    def _extract_guidance(self, content: str, ticker: str, prior_content: Optional[str] = None) -> dict:
        prior_section = ""
        if prior_content:
            prior_section = f"""
PRIOR QUARTER TRANSCRIPT (for comparison):
{prior_content[:3000]}
---
"""
        prompt = f"""You are a sell-side analyst tracking {ticker} guidance closely.
Extract all forward guidance from the current earnings call.

Return ONLY valid JSON (no markdown):
{{
  "next_quarter_guidance": {{
    "revenue": "guidance value or null",
    "gross_margin": "guidance value or null",
    "operating_expenses": "guidance value or null",
    "eps": "guidance value or null"
  }},
  "full_year_guidance": {{
    "revenue": "guidance value or null",
    "other_metrics": []
  }},
  "guidance_changes_vs_prior": [
    {{
      "metric": "e.g. Revenue",
      "prior_guidance": "what they said last quarter",
      "new_guidance": "what they say now",
      "direction": "raised",
      "magnitude": "e.g. +5% or qualitative description"
    }}
  ],
  "guidance_tone": "raised_guidance",
  "notable_guidance_items": ["item one", "item two"],
  "withdrawn_metrics": []
}}

Important: direction must be one of: raised, lowered, maintained, initiated, withdrawn.
Important: guidance_tone must be one of: raised_guidance, lowered_guidance, maintained, mixed.

{prior_section}
CURRENT TRANSCRIPT:
{content[:5000]}
"""
        return self._call_claude_json(prompt, "guidance")

    # ------------------------------------------------------------------
    # STEP 4 — Detect what topics are new, growing, or disappearing
    # ------------------------------------------------------------------

    def _detect_narrative_shifts(self, content: str, ticker: str, prior_content: Optional[str] = None) -> dict:
        prior_section = ""
        if prior_content:
            prior_section = f"""
PRIOR QUARTER TRANSCRIPT:
{prior_content[:3000]}
---
"""
        prompt = f"""You are a qualitative equity analyst specializing in narrative analysis.
Identify significant narrative shifts and new themes in this {ticker} earnings call.

Return ONLY valid JSON (no markdown). Use only plain ASCII characters in all strings.
Do not use special characters, curly quotes, or unicode in any string values.

{{
  "new_topics_introduced": [
    {{"topic": "topic name", "quote": "relevant quote using plain quotes only", "significance": "why this matters"}}
  ],
  "topics_increasing_in_emphasis": [
    {{"topic": "topic name", "evidence": "why you think emphasis increased"}}
  ],
  "topics_decreasing_or_dropped": [
    {{"topic": "topic name", "note": "why this might matter"}}
  ],
  "strategic_pivots": [
    {{"pivot": "description", "evidence": "quote or reference"}}
  ],
  "competitive_narrative": {{
    "competitors_mentioned": [],
    "competitive_tone": "neutral",
    "moat_language": ["quote one", "quote two"]
  }},
  "risk_factors_mentioned": [
    {{"risk": "description", "first_time_mentioned": false, "management_response": "how they addressed it"}}
  ],
  "key_narrative_delta": "1-2 sentence summary of most important narrative change this quarter"
}}

Important: first_time_mentioned must be true or false not a string.
Important: competitive_tone must be one of: aggressive, defensive, neutral.
Important: Use straight quotes only, never curly or smart quotes in your response.

{prior_section}
CURRENT TRANSCRIPT:
{content[:5000]}
"""
        return self._call_claude_json(prompt, "narrative")

    # ------------------------------------------------------------------
    # STEP 5 — Analyze the Q&A
    # ------------------------------------------------------------------

    def _analyze_qa(self, qa_content: str) -> dict:
        if not qa_content or len(qa_content) < 100:
            return {
                "qa_available": False,
                "note": "Q&A section not available or too short"
            }

        prompt = f"""You are an expert at analyzing earnings call Q&A sessions.
Analyze the questions analysts asked and how management responded.

Return ONLY valid JSON (no markdown). Use only plain ASCII characters in all strings.

{{
  "qa_available": true,
  "total_questions_estimated": 3,
  "top_analyst_concerns": [
    {{"concern": "topic", "frequency": "2 analysts asked", "management_response_quality": "direct"}}
  ],
  "evasiveness_score": 3,
  "evasiveness_examples": [
    {{"question_topic": "topic", "evasion_type": "vague", "quote": "brief relevant quote"}}
  ],
  "most_direct_answers": [
    {{"topic": "topic", "notable_because": "why this directness stands out"}}
  ],
  "surprise_questions": [
    {{"topic": "topic", "why_surprising": "what this reveals about investor concerns"}}
  ],
  "management_credibility_signals": [
    {{"signal": "description", "positive_or_negative": "positive"}}
  ],
  "key_qa_takeaway": "1-2 sentence summary of most important Q&A intelligence"
}}

Important: evasiveness_score must be a number not a string.
Important: qa_available must be true or false not a string.
Important: positive_or_negative must be either positive or negative.
Important: evasion_type must be one of: deflected, non-answer, vague.

Q&A TRANSCRIPT:
{qa_content[:4000]}
"""
        return self._call_claude_json(prompt, "qa_intelligence")

    # ------------------------------------------------------------------
    # DELTA SUMMARY
    # ------------------------------------------------------------------

    def _generate_delta_summary(self, current_kpis, current_tone, current_guidance,
                                 current_narrative, ticker, current_period, prior_period) -> str:
        prompt = f"""You are a portfolio manager writing a rapid delta note for your investment team.
Compare {ticker} {current_period} vs {prior_period} using the analysis data below.

Write 3-5 sentences covering:
1. The most important KPI change
2. Whether tone got better or worse
3. The most significant guidance change
4. The most important narrative shift

Be direct and specific. No fluff.

KPIs: {json.dumps(current_kpis)[:1500]}
Tone: {json.dumps(current_tone)[:1000]}
Guidance: {json.dumps(current_guidance)[:1000]}
Narrative: {json.dumps(current_narrative)[:1000]}
"""
        try:
            resp = self.client.messages.create(
                model=self.MODEL,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}]
            )
            return resp.content[0].text.strip()
        except Exception as e:
            return f"[Delta summary unavailable: {e}]"

    # ------------------------------------------------------------------
    # SIGNAL
    # ------------------------------------------------------------------

    def _determine_signal(self, kpis: dict, tone: dict, guidance: dict) -> str:
        score = 0

        conf = tone.get("overall_confidence_score", 5)
        if isinstance(conf, (int, float)):
            if conf >= 7:
                score += 1
            elif conf <= 4:
                score -= 1

        guidance_tone = guidance.get("guidance_tone", "")
        if "raised" in guidance_tone:
            score += 2
        elif "lowered" in guidance_tone:
            score -= 2

        gm_trend = kpis.get("gross_margin", {}).get("trend", "")
        if gm_trend == "expanding":
            score += 1
        elif gm_trend == "contracting":
            score -= 1

        if score >= 2:
            return "positive"
        elif score <= -2:
            return "negative"
        else:
            return "neutral"

    # ------------------------------------------------------------------
    # CLAUDE API HELPER — with multiple fallback parse attempts
    # ------------------------------------------------------------------

    def _call_claude_json(self, prompt: str, step_name: str) -> dict:
        try:
            resp = self.client.messages.create(
                model=self.MODEL,
                max_tokens=self.MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = resp.content[0].text.strip()

            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()

            # First attempt — clean parse
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass

            # Second attempt — fix common issues
            raw = raw.replace("\u2018", "'").replace("\u2019", "'")
            raw = raw.replace("\u201c", '"').replace("\u201d", '"')
            raw = re.sub(r",\s*}", "}", raw)
            raw = re.sub(r",\s*]", "]", raw)

            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass

            # Third attempt — extract JSON object
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass

            print(f"  [!] Could not parse JSON in {step_name} — skipping section")
            return {"error": f"Parse failed for {step_name}"}

        except Exception as e:
            print(f"  [!] Error in {step_name}: {e}")
            return {"error": str(e)}
