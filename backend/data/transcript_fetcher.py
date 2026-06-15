"""
transcript_fetcher.py
---------------------
Fetches earnings call transcripts from SEC EDGAR.
Completely free, no API key needed, never blocked.
"""

import json
import time
import requests
import re
import warnings
from pathlib import Path
from typing import Optional
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from dotenv import load_dotenv

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

load_dotenv()

CACHE_DIR = Path(__file__).parent.parent.parent / "data" / "transcripts"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Known CIKs for common tickers
KNOWN_CIKS = {
    "MSFT": "0000789019",
    "AAPL": "0000320193",
    "GOOGL": "0001652044",
    "GOOG":  "0001652044",
    "AMZN": "0001018724",
    "META": "0001326801",
    "NVDA": "0001045810",
    "TSLA": "0001318605",
    "NFLX": "0001065280",
    "AMD":  "0000002488",
    "INTC": "0000050863",
    "CRM":  "0001108524",
    "ORCL": "0001341439",
    "JPM":  "0000019617",
    "GS":   "0000886982",
    "BAC":  "0000070858",
}


class TranscriptFetcher:

    HEADERS = {
        "User-Agent": "EarningsResearchAgent research@example.com",
        "Accept-Encoding": "gzip, deflate",
        "Host": "www.sec.gov",
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)

    # ------------------------------------------------------------------
    # MAIN PUBLIC METHOD
    # ------------------------------------------------------------------

    def get_transcript(
        self,
        ticker: str,
        year: int,
        quarter: int,
        force_refresh: bool = False
    ) -> Optional[dict]:

        ticker = ticker.upper()
        cache_path = self._cache_path(ticker, year, quarter)

        if not force_refresh and cache_path.exists():
            print(f"[cache] Loading {ticker} Q{quarter} {year}")
            with open(cache_path, encoding="utf-8") as f:
                data = json.load(f)
                data["source"] = "cache"
                return data

        print(f"[edgar] Searching SEC EDGAR for {ticker} Q{quarter} {year}...")

        cik = KNOWN_CIKS.get(ticker) or self._get_cik(ticker)
        if not cik:
            print(f"[!] Could not find CIK for {ticker}")
            return None

        print(f"[edgar] CIK: {cik}")

        result = self._find_transcript_in_filings(cik, ticker, year, quarter)
        if result:
            self._save_cache(result, cache_path)
            return result

        print(f"[!] No transcript found for {ticker} Q{quarter} {year}")
        return None

    # ------------------------------------------------------------------
    # GET CIK
    # ------------------------------------------------------------------

    def _get_cik(self, ticker: str) -> Optional[str]:
        try:
            url = f"https://www.sec.gov/cgi-bin/browse-edgar?company=&CIK={ticker}&type=8-K&dateb=&owner=include&count=5&search_text=&action=getcompany&output=atom"
            resp = self.session.get(url, timeout=15)
            soup = BeautifulSoup(resp.text, "lxml")
            cik_tag = soup.find("cik")
            if cik_tag:
                return cik_tag.text.strip().zfill(10)
        except Exception as e:
            print(f"[cik error] {e}")
        return None

    # ------------------------------------------------------------------
    # FIND TRANSCRIPT IN 8-K FILINGS
    # Looks through all recent 8-K filings, wider date window
    # ------------------------------------------------------------------

    def _find_transcript_in_filings(
        self, cik: str, ticker: str, year: int, quarter: int
    ) -> Optional[dict]:

        try:
            # Get all 8-K filings for this company (up to 40)
            url = (
                f"https://www.sec.gov/cgi-bin/browse-edgar"
                f"?action=getcompany&CIK={cik}&type=8-K"
                f"&dateb=&owner=include&count=40&search_text=&output=atom"
            )
            resp = self.session.get(url, timeout=15)
            soup = BeautifulSoup(resp.text, "lxml")
            entries = soup.find_all("entry")
            print(f"[edgar] Scanning {len(entries)} 8-K filings...")

            # Build wide date window — entire year around the quarter
            # This handles fiscal year differences across companies
            search_year_start = f"{year}-01-01"
            search_year_end = f"{year + 1}-06-30"

            for entry in entries:
                updated = entry.find("updated")
                if not updated:
                    continue

                filing_date = updated.text[:10]

                # Only look within a reasonable window
                if not (search_year_start <= filing_date <= search_year_end):
                    continue

                link = entry.find("link")
                if not link:
                    continue

                filing_index_url = link.get("href", "")
                if not filing_index_url:
                    continue

                time.sleep(0.3)
                result = self._extract_from_filing_index(
                    filing_index_url, ticker, year, quarter, filing_date
                )
                if result:
                    return result

        except Exception as e:
            print(f"[filings error] {e}")

        return None

    # ------------------------------------------------------------------
    # EXTRACT TRANSCRIPT FROM FILING INDEX PAGE
    # ------------------------------------------------------------------

    def _extract_from_filing_index(
        self,
        index_url: str,
        ticker: str,
        year: int,
        quarter: int,
        filing_date: str
    ) -> Optional[dict]:
        try:
            resp = self.session.get(index_url, timeout=15)
            if resp.status_code != 200:
                return None

            soup = BeautifulSoup(resp.text, "lxml")

            # Look for exhibit files that might contain transcript
            # Earnings transcripts are typically filed as EX-99.1
            exhibit_links = []
            for a in soup.find_all("a", href=True):
                href = a["href"]
                link_text = a.get_text(strip=True).lower()

                is_exhibit = any(x in link_text for x in [
                    "ex-99", "ex 99", "exhibit 99", "99.1", "transcript",
                    "earnings", "press release"
                ])

                is_document = href.endswith((".htm", ".html", ".txt"))

                if is_exhibit and is_document:
                    full_url = f"https://www.sec.gov{href}" if href.startswith("/") else href
                    exhibit_links.append(full_url)

            # Also grab all .htm files from the filing as fallback
            if not exhibit_links:
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if href.endswith((".htm", ".html")) and "/Archives/" in href:
                        full_url = f"https://www.sec.gov{href}" if href.startswith("/") else href
                        exhibit_links.append(full_url)

            # Try each exhibit
            for exhibit_url in exhibit_links[:5]:
                time.sleep(0.3)
                text = self._extract_text_from_url(exhibit_url)
                if text and len(text) > 1000 and self._looks_like_transcript(text):
                    print(f"[edgar] Found transcript in filing dated {filing_date}")
                    return {
                        "ticker": ticker,
                        "year": year,
                        "quarter": quarter,
                        "date": filing_date,
                        "title": f"{ticker} Q{quarter} {year} Earnings Call",
                        "content": text,
                        "sections": self._parse_sections(text),
                        "source": "sec_edgar",
                        "url": exhibit_url,
                    }

        except Exception as e:
            print(f"[index error] {e}")

        return None

    # ------------------------------------------------------------------
    # EXTRACT CLEAN TEXT FROM A URL
    # ------------------------------------------------------------------

    def _extract_text_from_url(self, url: str) -> Optional[str]:
        try:
            resp = self.session.get(url, timeout=15)
            if resp.status_code != 200:
                return None

            content_type = resp.headers.get("content-type", "")

            if "html" in content_type or url.endswith((".htm", ".html")):
                soup = BeautifulSoup(resp.text, "lxml")
                for tag in soup.find_all(["script", "style", "nav", "header", "footer"]):
                    tag.decompose()
                text = soup.get_text(separator="\n")
            else:
                text = resp.text

            return self._clean_text(text)

        except Exception as e:
            print(f"[extract error] {e}")
        return None

    # ------------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------------

    def _looks_like_transcript(self, text: str) -> bool:
        signals = [
            "operator", "ceo", "cfo", "earnings", "revenue",
            "quarter", "guidance", "per share", "thank you",
            "question", "analyst", "billion", "million",
            "good morning", "good afternoon", "good evening",
        ]
        text_lower = text.lower()
        matches = sum(1 for s in signals if s in text_lower)
        return matches >= 5

    def _clean_text(self, text: str) -> str:
        lines = []
        for line in text.split("\n"):
            line = line.strip()
            if len(line) > 1:
                lines.append(line)
        return "\n".join(lines)

    def _parse_sections(self, content: str) -> dict:
        if not content:
            return {"prepared_remarks": "", "qa": ""}

        qa_markers = [
            "questions and answers",
            "question-and-answer",
            "q&a session",
            "operator:",
            "we will now begin the question",
            "open the call for questions",
        ]

        content_lower = content.lower()
        split_pos = len(content)

        for marker in qa_markers:
            pos = content_lower.find(marker)
            if pos != -1 and pos < split_pos:
                split_pos = pos

        return {
            "prepared_remarks": content[:split_pos].strip(),
            "qa": content[split_pos:].strip(),
        }

    def _cache_path(self, ticker: str, year: int, quarter: int) -> Path:
        return CACHE_DIR / f"{ticker}_{year}_Q{quarter}.json"

    def _save_cache(self, data: dict, path: Path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"[cache] Saved to {path.name}")


# ------------------------------------------------------------------
# DEMO TRANSCRIPT
# ------------------------------------------------------------------

DEMO_TRANSCRIPT = {
    "ticker": "NVDA",
    "year": 2024,
    "quarter": 4,
    "date": "2024-02-21",
    "title": "NVIDIA Q4 FY2024 Earnings Call",
    "content": """
NVIDIA Corporation (NVDA) Q4 FY2024 Earnings Call
February 21, 2024

Simona Jankowski - VP Investor Relations:
Thank you. Good afternoon everyone. I am Simona Jankowski, VP of Investor Relations at NVIDIA.
With me today are Jensen Huang, President and Chief Executive Officer, and Colette Kress,
Executive Vice President and Chief Financial Officer.

Jensen Huang - CEO:
Thank you Simona. NVIDIA had an exceptional quarter. Revenue was $22.1 billion, up 22% sequentially
and up 265% year over year. We exceeded our outlook by approximately $2 billion.

Our Data Center platform had revenue of $18.4 billion, up 27% sequentially and up 409% year over year.
Demand for our H100 GPUs continues to be extraordinary. We are seeing strong adoption from cloud
service providers, consumer internet companies, and enterprise customers.

Looking ahead to Q1 FY2025, we expect revenue of $24 billion plus or minus 2%. We expect our
supply to continue to improve throughout the year. We are in full production of H200, and our
Blackwell platform is on track.

Gross margins reached a record 76.7% for the quarter. We expect gross margins in the mid-70s
going forward. Our CUDA ecosystem now has over 4 million developers, up from 3 million a year ago.

Colette Kress - CFO:
Thank you Jensen. Q4 revenue of $22.1 billion exceeded our outlook of $20 billion. Full year revenue
was $60.9 billion, up 122% from the prior year. Gaming revenue was $2.9 billion in Q4, up 56% year
on year. Professional Visualization revenue was $463 million, up 105% year over year.

For Q1 FY2025, we expect: Revenue of $24.0 billion, plus or minus 2%. GAAP gross margin of 76.3%,
plus or minus 50 basis points. GAAP operating expenses of approximately $3.5 billion.

Questions and Answers:

Operator: Our first question comes from Vivek Arya from Bank of America.

Vivek Arya - Bank of America:
Jensen, can you talk about visibility into H200 and Blackwell demand?

Jensen Huang - CEO:
The visibility is exceptional. The demand for Blackwell is extraordinary. Every customer I talk to
wants as much Blackwell as we can give them. The concern I hear is not whether to buy, it is whether
they can get supply. I am very confident in our supply trajectory.

Operator: Our next question comes from Timothy Arcuri from UBS.

Timothy Arcuri - UBS:
Colette, can you help us think about gross margin sustainability?

Colette Kress - CFO:
For Q1, we expect gross margins in the mid-70s. New product ramps initially come in at slightly lower
margins but we expect margins to remain healthy and sustainable.
""",
    "sections": {"prepared_remarks": "", "qa": ""},
    "source": "demo"
}


def get_demo_transcript() -> dict:
    fetcher = TranscriptFetcher()
    demo = DEMO_TRANSCRIPT.copy()
    demo["sections"] = fetcher._parse_sections(demo["content"])
    return demo
