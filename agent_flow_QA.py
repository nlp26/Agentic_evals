import os, time, json, argparse
from datetime import datetime
from pathlib import Path
import pandas as pd
from openai import OpenAI

MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1")
RUNS  = Path("./runs").resolve()
client = OpenAI()

def _poll(thread_id: str, run_id: str):
    while True:
        run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run_id)
        if run.status in {"queued","in_progress","cancelling"}:
            time.sleep(0.6); continue
        return run

def _get_id(envvar: str) -> str:
    v = os.getenv(envvar)
    if not v: raise RuntimeError(f"Missing {envvar}. export {envvar}=asst_...")
    return v

def run_planner_and_get_text(planner_id: str, brief: str) -> str:
    th = client.beta.threads.create()
    client.beta.threads.messages.create(thread_id=th.id, role="user", content=brief)
    run = client.beta.threads.runs.create(thread_id=th.id, assistant_id=planner_id)
    run = _poll(th.id, run.id)
    if run.status != "completed":
        raise RuntimeError(f"Planner run not completed: {run.status}")
    msgs = client.beta.threads.messages.list(thread_id=th.id)
    for m in msgs.data:
        if m.role == "assistant":
            parts = []
            for c in m.content:
                if getattr(c, "type", None) == "text" and getattr(c, "text", None):
                    parts.append(c.text.value)
            if parts:
                return "\n".join(parts)
    raise RuntimeError("Planner produced no text.")

def run_qa_and_get_json(qa_id: str, plan_text: str) -> dict:
    """QA must have a function tool 'emit_review' (strict JSON schema)."""
    th = client.beta.threads.create()
    client.beta.threads.messages.create(
        thread_id=th.id, role="user",
        content=(
            "Audit this human-text media plan. Parse fully, validate only active modules, "
            "and return ONE function call to emit_review with strict JSON.\n\n" + plan_text
        )
    )
    run = client.beta.threads.runs.create(thread_id=th.id, assistant_id=qa_id)
    run = _poll(th.id, run.id)

    if run.status != "requires_action":
        msgs = client.beta.threads.messages.list(thread_id=th.id)
        last = ""
        if msgs.data:
            for c in msgs.data[0].content:
                if getattr(c, "type", None) == "text" and getattr(c, "text", None):
                    last += c.text.value
        raise RuntimeError("QA did not call emit_review (function). Last message:\n" + last)

    tcs = run.required_action.submit_tool_outputs.tool_calls
    if not tcs:
        raise RuntimeError("No tool_calls present on QA run.")
    args_raw = tcs[0].function.arguments or "{}"
    data = json.loads(args_raw)

    # Acknowledge tool outputs so the run can complete
    client.beta.threads.runs.submit_tool_outputs(
        thread_id=th.id, run_id=run.id,
        tool_outputs=[{"tool_call_id": tc.id, "output": "ack"} for tc in tcs],
    )
    _poll(th.id, run.id)
    return data

def export_excel(xlsx_path: Path, qa_json: dict):
    # Summaries
    summary_rows = [{
        "advertiser": qa_json.get("extracted", {}).get("advertiser_domain",""),
        "objective": qa_json.get("extracted", {}).get("objective",""),
        "budget_total": qa_json.get("extracted", {}).get("budget_total",""),
        "start_date": qa_json.get("extracted", {}).get("start_date",""),
        "end_date": qa_json.get("extracted", {}).get("end_date",""),
        "verdict": qa_json.get("verdict",""),
        "score": qa_json.get("overall_score",""),
        "matches_budget_total": qa_json.get("budget_check",{}).get("matches_budget_total",""),
        "diff": qa_json.get("budget_check",{}).get("diff",""),
    }]
    df_summary = pd.DataFrame(summary_rows)

    # Checklist table
    checks = qa_json.get("checklist", [])
    df_checks = pd.DataFrame(checks) if checks else pd.DataFrame(columns=["id","section","description","critical","status","notes"])

    with pd.ExcelWriter(xlsx_path, engine="xlsxwriter") as xw:
        df_summary.to_excel(xw, sheet_name="Summary", index=False)
        df_checks.to_excel(xw, sheet_name="Checklist", index=False)

        # Simple pass/fail bar chart
        if not df_checks.empty and "status" in df_checks:
            counts = df_checks["status"].value_counts().reset_index()
            counts.columns = ["status","count"]
            counts.to_excel(xw, sheet_name="Stats", index=False)
            wb = xw.book
            ws = xw.sheets["Stats"]
            n = len(counts)
            ch = wb.add_chart({"type":"column"})
            ch.add_series({
                "name":"Checks by status",
                "categories":["Stats", 1, 0, n, 0],
                "values":    ["Stats", 1, 1, n, 1],
            })
            ch.set_title({"name":"QA Checks"})
            ch.set_x_axis({"name":"status"})
            ch.set_y_axis({"name":"count"})
            ws.insert_chart("E2", ch)

def save_run(plan_text: str, qa_json: dict, planner_id: str | None, qa_id: str) -> Path:
    RUNS.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    rdir = RUNS / ts
    rdir.mkdir(parents=True, exist_ok=True)
    (rdir/"plan.txt").write_text(plan_text, encoding="utf-8")
    (rdir/"qa.json").write_text(json.dumps(qa_json, indent=2), encoding="utf-8")
    (rdir/"meta.json").write_text(json.dumps({
        "planner_id": planner_id, "qa_id": qa_id, "model": MODEL, "created_at": datetime.now().isoformat()
    }, indent=2), encoding="utf-8")
    export_excel(rdir/"audit_report.xlsx", qa_json)
    return rdir

def main():
    ap = argparse.ArgumentParser(description="Planner (text) → QA (strict JSON) using Assistant IDs")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--brief", help="One-sentence brief to send to the Planner assistant.")
    g.add_argument("--plan-file", help="Path to a text file containing a media plan (human text).")
    args = ap.parse_args()

    qa_id = _get_id("QA_ID")

    if args.plan_file:
        planner_id = None
        plan_text = Path(args.plan_file).read_text(encoding="utf-8")
    else:
        planner_id = _get_id("PLANNER_ID")
        plan_text = run_planner_and_get_text(planner_id, args.brief)

    qa_json = run_qa_and_get_json(qa_id, plan_text)
    rdir = save_run(plan_text, qa_json, planner_id, qa_id)

    # Console mini-dashboard
    verdict = qa_json.get("verdict")
    score = qa_json.get("overall_score")
    issues = qa_json.get("issues", [])[:5]
    print(f"\nSaved → {rdir}")
    print(f"QA verdict: {verdict} | score: {score}")
    if issues:
        print("Key issues:")
        for i in issues:
            print(" - " + i)

if __name__ == "__main__":
    main()



