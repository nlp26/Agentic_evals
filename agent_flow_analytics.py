#!/usr/bin/env python3
import os
import time
import json
import argparse
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any
import pandas as pd

import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

import logging
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

from openai import OpenAI

# Configurações
MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1")  
RUNS_DIR = Path("./runs").resolve()
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

logger = logging.getLogger(__name__)
client = OpenAI()


def _poll(thread_id: str, run_id: str, interval: float = 2.0) -> Any:
    """Wait for a run to complete by polling its status until it
    leaves queued|in_progress|cancelling."""
    while True:
        run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run_id)
        status = run.status
        if status in {"queued", "in_progress", "cancelling"}:
            time.sleep(interval)
            continue
        return run


def _get_env_var(name: str) -> str:
    """Retrieves and validates environment variable."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing {name}. Execute: export {name}=<assistant_id>")
    return value


def run_planner_and_get_text(planner_id: str, brief: str) -> str:
    """Calls the planner assistant and returns the generated text."""
    logger.info("Sending brief to the Planner assistant...")
    th = client.beta.threads.create()
    client.beta.threads.messages.create(thread_id=th.id, role="user", content=brief)
    run = client.beta.threads.runs.create(thread_id=th.id, assistant_id=planner_id)
    run = _poll(th.id, run.id)
    if run.status != "completed":
        raise RuntimeError(f"Planner run failed with status {run.status}")
    msgs = client.beta.threads.messages.list(thread_id=th.id)
    texts = []
    for m in msgs.data:
        if m.role == "assistant":
            for part in m.content:
                if getattr(part, "type", None) == "text" and getattr(part, "text", None):
                    texts.append(part.text.value)
    if not texts:
        raise RuntimeError("Planner produced no output.")
    return "\n".join(texts)


def run_qa_and_get_json(qa_id: str, plan_text: str) -> Dict[str, Any]:
    """Calls the QA assistant and returns the structured JSON."""
    logger.info("Sending plan to QA assistant...")
    th = client.beta.threads.create()
    client.beta.threads.messages.create(
        thread_id=th.id,
        role="user",
        content=(
            "Audit this human-text media plan. Parse fully, validate only active modules, "
            "and return ONE function call to emit_review with strict JSON.\n\n"
            + plan_text
        ),
    )
    run = client.beta.threads.runs.create(thread_id=th.id, assistant_id=qa_id)
    run = _poll(th.id, run.id)

    if run.status == "requires_action":
        calls = run.required_action.submit_tool_outputs.tool_calls
        if calls:
            raw_args = calls[0].function.arguments or "{}"
            data = json.loads(raw_args)
            # Acknowledge tool call
            client.beta.threads.runs.submit_tool_outputs(
                thread_id=th.id,
                run_id=run.id,
                tool_outputs=[{"tool_call_id": c.id, "output": "ack"} for c in calls],
            )
            _poll(th.id, run.id)
            return data

    # Fallback: Se não houver tool call, verifique se o JSON está na última mensagem
    msgs = client.beta.threads.messages.list(thread_id=th.id)
    if msgs.data:
        last_content = ""
        for c in msgs.data[0].content:
            if getattr(c, "type", None) == "text" and getattr(c, "text", None):
                last_content += c.text.value
        try:
            # Tenta parsear o JSON da mensagem (removendo possíveis wrappers)
            data = json.loads(last_content.strip())
            return data
        except json.JSONDecodeError:
            pass  # Se não for JSON válido, prossegue para erro

    raise RuntimeError("QA did not produce valid JSON via tool or message.")


def export_csvs(output_dir: Path, qa_json: Dict[str, Any]) -> None:
    """Gera três CSVs: summary, checklist and stats."""
    logger.info("Exportando CSVs...")
    try:
        # 1) Summary
        summary = qa_json.get("extracted_elements", {}).get("summary", {})
        row = {
            "domain": summary.get("domain", ""),
            "objective": summary.get("objective", ""),
            "start_dates": summary.get("dates", ""),
            "budget_total": summary.get("totals", {}).get("Total", {}).get("cost", ""),
            "verdict": qa_json.get("verdict", ""),
            "score": qa_json.get("overall_score", ""),
        }
        summary_path = output_dir / "summary.csv"
        pd.DataFrame([row]).to_csv(summary_path, index=False)
        logger.info(f"CSV gerado: {summary_path} (1 linha)")

        # 2) Checklist
        checklist = qa_json.get("checklist", [])
        if checklist:
            df_checks = pd.DataFrame([{
                "goal": item.get("goal", ""),
                "status": item.get("status", ""),
                "justification": item.get("justification", "")
            } for item in checklist])
        else:
            df_checks = pd.DataFrame(columns=["goal", "status", "justification"])
        checklist_path = output_dir / "checklist.csv"
        df_checks.to_csv(checklist_path, index=False)
        logger.info(f"CSV gerado: {checklist_path} ({len(df_checks)} linhas)")

        # 3) Stats (status counts)
        if not df_checks.empty:
            stats = df_checks["status"].value_counts().rename_axis("status").reset_index(name="count")
        else:
            stats = pd.DataFrame(columns=["status", "count"])
        stats_path = output_dir / "stats.csv"
        stats.to_csv(stats_path, index=False)
        logger.info(f"CSV gerado: {stats_path} ({len(stats)} linhas)")

    except Exception as e:
        logger.error(f"Erro ao exportar CSVs: {str(e)}")
        raise  # Re-raise para não silenciar o erro

def save_run(
    plan_text: str,
    qa_json: Dict[str, Any],
    planner_id: Optional[str],
    qa_id: str
) -> Path:
    """
    Stores plan.txt, qa.json, meta.json and the three CSVs
    in a timestamped folder, then returns its Path.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    rdir = RUNS_DIR / ts
    rdir.mkdir(parents=True, exist_ok=True)

    # Save raw outputs
    (rdir / "plan.txt").write_text(plan_text, encoding="utf-8")
    (rdir / "qa.json").write_text(json.dumps(qa_json, indent=2), encoding="utf-8")

    # Save metadata
    meta = {
        "planner_id": planner_id,
        "qa_id": qa_id,
        "model": MODEL,
        "created_at": datetime.now().isoformat()
    }
    (rdir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # Generate CSVs
    export_csvs(rdir, qa_json)

    # **Return the actual directory** 
    return rdir


def main():
    parser = argparse.ArgumentParser(
        description="Pipeline: Planner → QA → export CSV analytics"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--brief", help="Brief for the Planner assistant.")
    group.add_argument("--plan-file", help="Text file of the media plan.")
    args = parser.parse_args()

    qa_id = _get_env_var("QA_ID")

    if args.plan_file:
        planner_id = None
        plan = Path(args.plan_file).read_text(encoding="utf-8")
    else:
        planner_id = _get_env_var("PLANNER_ID")
        plan = run_planner_and_get_text(planner_id, args.brief)

    qa_output = run_qa_and_get_json(qa_id, plan)
    run_dir = save_run(plan, qa_output, planner_id, qa_id)

    # Resumo no console
    verdict = qa_output.get("verdict")
    score = qa_output.get("overall_score")
    logger.info(f"Run saved em {run_dir}")
    logger.info(f"QA verdict: {verdict} | score: {score}")

if __name__ == "__main__":
    main()




