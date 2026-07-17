# Architectural Decisions

This document outlines the design decisions made to optimize for a time-boxed 6-8 hour assignment while maintaining production-grade standards.

## 1. Data Storage: Local JSON over SQLite/PostgreSQL
**Decision:** Store intermediate and final states in local JSON files (`data/research.json`, `data/verified.json`).
**Reasoning:** In a short assessment, standing up and debugging database connections or ORMs (like SQLAlchemy) introduces unnecessary overhead. JSON provides an instant, schema-less document store that is trivially readable and writable natively in Python. It's sufficient for 100 records and makes manual inspection during development instantaneous.

## 2. LLM Extraction: `instructor` + Pydantic
**Decision:** Use the `instructor` library wrapped around the OpenAI API with Pydantic models.
**Reasoning:** Prompt engineering for strict JSON output is brittle and time-consuming. `instructor` natively leverages OpenAI's function calling to guarantee that the output perfectly matches our Pydantic schema, automatically handling validation and retries.

## 3. Scraping Strategy: HTTPX + BeautifulSoup over Playwright
**Decision:** Start with standard HTTP fetching and BeautifulSoup. Playwright is left optional.
**Reasoning:** While Playwright handles JS-rendered Single Page Applications (SPAs) better, it introduces significant execution overhead and installation complexity (browsers binaries). For an MVP, `httpx` will fetch 90% of what is needed (especially for docs built on static site generators). We will fall back to LLM general knowledge if scraping fails, and flag it for human review.

## 4. Orchestration: Native Python over LangGraph/Prefect
**Decision:** Write the orchestration loop purely in Python (`main.py`) rather than using complex frameworks.
**Reasoning:** Frameworks like Prefect or LangGraph are powerful but require steep learning curves and boilerplate. For a batch job of 100 items, a well-structured Python loop with `try/except` and resume-from-state logic (checking if an app is already in `research.json`) is far more efficient to build in 6 hours.

## 5. HITL Implementation: CLI Prompt
**Decision:** For the Human-In-The-Loop review, use a simple interactive CLI interface instead of a full web dashboard (like Streamlit).
**Reasoning:** Building a CRUD web dashboard, even in Streamlit, takes time away from the core AI extraction logic. A CLI that pauses, prints the flagged data, and asks `[A]pprove, [R]eject, [E]dit` meets the assessment requirements cleanly.
