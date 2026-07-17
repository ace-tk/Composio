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

## Repository

This project was built as part of the **Composio AI Product Ops Intern** take-home assignment.
