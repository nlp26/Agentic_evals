# agentic-evals

Scripts for evaluating OpenAI Assistants — exercising the Assistants v2 API, the Evals API, and a few tool integrations (web search, knowledge base scraping). Built as a sandbox while figuring out what's worth measuring on a real agent flow.

For a paper-grounded judge with rubrics, CoT scoring, and bias calibration, see [`nlp26/agentic-papers/agent_as_judge`](https://github.com/nlp26/agentic-papers/tree/main/agent_as_judge).

## What's inside

**Agent flows (Assistants API v2)**
- `agent_flow_QA.py` — planner agent that turns a brief into a Q&A run; polls until complete.
- `agent_flow_analytics.py` — same shape, analytics-focused agent, structured logging into `./runs/`.

**Tool tests**
- `websearch_test.py`, `websearch_test2.py` — `WebSearchTool` against a configured Assistant.
- `kb_scrape_to_json.py` — scrape a public KB site, dump structured JSON.
- `kb_scrape_to_json_cdp.py` — same intent, via Chrome DevTools Protocol (for JS-rendered KBs).

**Evals**
- `eval-agent.py` — uses the OpenAI Evals API. Defines a `prompt`/`completion` schema, creates the eval, attaches testing criteria.
- `agent_test.py`, `automated_test6.py` — local test runners for the agent flows above.

**Sample output**
- `agent_eval_report_20250.md` — an eval report from a prior run.

## Run

```bash
pip install openai pandas python-dotenv nest-asyncio
export OPENAI_API_KEY=...

# Agent flows need assistant IDs in env (asst_...):
export PLANNER_ASSISTANT_ID=asst_...
python agent_flow_QA.py "your brief here"

# Evals:
python eval-agent.py
```

Outputs land in `./runs/<timestamp>/`.

## License

Apache-2.0
