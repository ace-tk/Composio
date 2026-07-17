# Composio SaaS Research System

An AI-powered multi-agent system that researches 100 SaaS applications, extracts structured integration metadata, verifies the findings, and generates an interactive HTML case study.

## Features

- Multi-agent research pipeline
- Automated verification with confidence scoring
- Human-in-the-Loop (HITL) fallback
- Structured JSON outputs
- Interactive HTML report

## Architecture

```text
                 apps.csv
                     │
                     ▼
            Research Agent
                     │
                     ▼
            Verifier Agent
                     │
        ┌────────────┴────────────┐
        │                         │
        ▼                         ▼
   Verified                 Manual Review
        │                         │
        └────────────┬────────────┘
                     ▼
             Analyst Agent
                     │
                     ▼
        Interactive HTML Report
```

## Project Structure

```
agents/
utils/
data/
templates/
main.py
```

## Setup

Create a virtual environment and install dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file:

```env
GROQ_API_KEY=your_api_key
```

## Run

```bash
python main.py
```

## Output

Running the pipeline generates:

- `research.json` – Research results
- `verified.json` – Verified applications
- `verification_report.json` – Verification summary
- `workflow.json` – Workflow metadata
- `insights.json` – Pattern analysis
- `case_studies.json` – Generated case studies

The final HTML report is generated from these outputs.

## Dashboard (local)

Serve the static site from `templates/` so `/data/*.json` resolves correctly:

```bash
cd templates
python3 -m http.server 8000
```

Open http://localhost:8000

Pipeline runs also copy JSON artifacts into `templates/data/` automatically.

## Future Improvements

- Integrate Composio SDK for native tool execution
- Add incremental update support
- Schedule automatic pipeline runs
- Add confidence scoring using LLM evaluation

## Deploy (Vercel)

1. Set **Root Directory** to `templates` (keep this setting).
2. Deploy — `templates/data/*.json` is included in the static site.
3. After re-running the pipeline, commit the updated `templates/data/` files and redeploy.


## Repository

This project was built as part of the **Composio AI Product Ops Intern** take-home assignment.
