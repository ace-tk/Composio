# Composio SaaS Research & Verification System

An autonomous, multi-agent AI pipeline designed to discover, extract, verify, and analyze integration metadata for SaaS applications. Built to prioritize accuracy over speed, the system utilizes a robust automated verification layer and a structured Human-in-the-Loop (HITL) review routing mechanism to handle documentation ambiguities and scraping blocks without compromising data integrity.

---

## Architecture

The project employs a modular, multi-agent architecture where agents perform specialized tasks in sequence, communicating through structured JSON schema contracts:

```
    [data/apps.csv]
           │
           ▼
   [Research Agent]  ──(Crawls and extracts raw metadata)
           │
           ▼
   [Verifier Agent]  ──(Audits claims field-by-field against live docs)
           │
           ├──► [VERIFIED]
           └──► [MANUAL REVIEW] ──(Human-in-the-Loop Fallback)
           │
           ▼
   [Analyst Agent]   ──(Extracts trends & synthesizes insights)
           │
           ▼
[Interactive HTML Case Study]
```

1. **Research Agent**: Crawls developer documentation to extract metadata (authentication methods, API surfaces, self-serve access, buildability) using structured Pydantic schemas.
2. **Verifier Agent**: Audits extracted claims field-by-field against live source URLs. It applies a weighted confidence adjustment and routes ambiguous or contradicted claims to Human Review.
3. **Analyst Agent**: Evaluates the compiled, verified dataset to extract trends, failure patterns, and high-level integration insights.
4. **Human-in-the-Loop (HITL) Fallback**: Flags and routes records with low confidence, missing evidence, or documentation conflicts for manual human review instead of marking them as failed.

---

## Features

- **Multi-Agent Architecture**: Decoupled agents (Research, Verifier, Analyst) orchestrating distinct execution phases.
- **Autonomous SaaS Research**: Automatic documentation discovery and metadata extraction.
- **Automated Verification**: Rigorous claim verification against source documentation text.
- **Human-in-the-Loop Workflow**: Ambiguous, incomplete, or contradicted claims are isolated for manual review.
- **Structured JSON Outputs**: Pydantic-enforced schemas validating all inputs/outputs.
- **Confidence Scoring**: Dynamic, weighted confidence system determining verification status.
- **Retry-Aware Scraping**: Resilient HTTP client with retry logic and exponential backoff.
- **Structured Failure Reporting**: Granular categorization of failures (e.g., Rate Limits, Fetch Failures).
- **Interactive HTML Case Study**: Beautiful executive dashboard visualizing the dataset, KPIs, and analysis.

---

## Tech Stack

- **Core**: Python 3.10+
- **LLM Orchestration**: Groq API, Instructor, OpenAI SDK
- **Data Validation**: Pydantic v2
- **Networking & Scraping**: httpx, BeautifulSoup4
- **Concurrency**: AsyncIO
- **Templating**: Jinja2

---

## Project Structure

```
composio-assessment/
├── agents/             # Agent implementations (researcher, verifier, analyst)
├── data/               # Input data (apps.csv) and generated JSON artifacts
├── utils/              # Shared utilities (crawling, models, schemas)
├── templates/          # Frontend assets and HTML dashboard templates
├── requirements.txt    # Python dependencies
└── main.py             # Pipeline entry point & orchestrator
```

---

## Setup Instructions

### 1. Clone & Environment Setup
Navigate to the project directory, initialize a virtual environment, and install dependencies:
```bash
cd composio-assessment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Environment Variables
Create a `.env` file in the root directory:
```env
GROQ_API_KEY=your_groq_api_key_here
```

### 3. Execution
Run the end-to-end pipeline:
```bash
python main.py
```

---

## Generated Outputs

All pipeline results are written to the `data/` directory:

- **`research.json`**: Contains the raw metadata and source evidence extracted by the Research Agent.
- **`verified.json`**: Holds the final audited data after passing through verification and review routing.
- **`verification_report.json`**: Summarizes pipeline metrics, common failure reasons, and structured failure categories.
- **`workflow.json`**: Provides pipeline execution meta-statistics for the frontend dashboard.
- **`insights.json`**: Synthesizes high-level trend analysis and integration complexity reports.
- **`case_studies.json`**: Houses detailed deep-dives of selected verified platforms.

---

## Reliability Improvements

- **Retry Logic & Backoff**: Robust 3× retries with exponential backoff on transient network, timeout, and HTTP 429 rate limit errors (implemented for both scraper and LLM calls).
- **Automatic Redirect Handling**: The scraper follows redirects automatically to capture final destination URLs.
- **Browser-Like Request Headers**: Full multi-header user agent styling is used to reduce bot-blocking.
- **Graceful Timeout Management**: Request timeouts prevent hanging execution threads.
- **Structured Logging**: Descriptive, unified stdout recording success metrics, latencies, and failure types.
- **Weighted Verification**: Claims are audited independently. Critical fields (e.g., auth, API surface) are weighted higher than non-critical fields (e.g., MCP).
- **Human Review Fallback**: Unresolved rates, validation errors, or documentation contradictions default cleanly to human review rather than causing pipeline failures.

---

## Latest Pipeline Run

The pipeline was executed against the full batch of 104 applications. The results are summarized below:

| Metric | Value |
| :--- | :--- |
| **Applications Processed** | 104 |
| **Automatically Verified** | 28 |
| **Human Review Required** | 36 |
| **Failed Extractions** | 40 |
| **Average AI Confidence** | 47.61% |

---

## Known Limitations

- **Anti-Bot Blocking**: Certain SaaS portals actively block non-browser requests, occasionally leading to fetch failures.
- **API Rate Limits**: Groq free-tier rate limits (TPM/RPM constraints) can throttle the pipeline speed under high concurrency.
- **Ambiguous Documentation**: If a service's documentation is silent or contradictory on critical integration paths, the system routes the app to Human Review instead of making assumptions.

---

## Future Improvements

- **Parallel Verification**: Utilizing AsyncIO gathers to verify multiple records concurrently.
- **Verification Caching**: Caching fetch responses to prevent redundant scraping of shared documentation endpoints.
- **Playwright-Based Scraping**: Integrating headless browsers to bypass heavy JavaScript and cloud protection blocks.
- **Multi-Model Verification**: Cross-validating LLM verdicts across multiple models (e.g., Llama + Claude) to minimize hallucinations.
- **Monitoring Dashboard**: A live web dashboard to visualize pipeline progress and inspect the manual review queue in real-time.
