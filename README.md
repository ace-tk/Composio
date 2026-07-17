# Composio SaaS Research System

## Project Goals
The goal of this project is to build an AI-powered research agent capable of autonomously analyzing 100 SaaS applications. It extracts specific parameters such as Authentication methods, API surfaces, self-serve access, and buildability. Crucially, the system focuses on accuracy over speed, incorporating an automated verification process and a Human-in-the-Loop (HITL) fallback mechanism for ambiguous data.

The final output is a beautiful, single-file HTML report summarizing insights, failure points, and the verified directory of all researched apps.

## Architecture
The project follows a multi-agent, batch-processing architecture:
1. **Researcher Agent:** Fetches documentation, parses raw HTML text, and structures data via Pydantic.
2. **Verifier Agent:** Independently checks the extracted data against the source URLs, assigning a confidence score.
3. **Analyst Agent:** Reviews the full dataset to find trends and synthesize high-level insights.
4. **HITL Workflow:** Fallback for apps marked below the confidence threshold.

## Setup Instructions

1. **Clone & Environment:**
   ```bash
   cd composio-assessment
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Environment Variables:**
   Create a `.env` file and add your API keys:
   ```env
   OPENAI_API_KEY=your_key_here
   ```

3. **Execution:**
   ```bash
   python main.py
   ```

## Planned Workflow
1. Load target SaaS list from `data/apps.csv`.
2. The `Researcher` processes each app, storing raw structured results in `data/research.json`.
3. The `Verifier` processes `research.json` and approves or flags items.
4. Human reviews flagged items in a simple CLI/UI. Verified output is saved to `data/verified.json`.
5. The `Analyst` reads `verified.json`, compiles metrics, and Jinja2 renders `output/report.html`.
