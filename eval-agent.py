# eval_quickstart.py

import os
import openai
from dotenv import load_dotenv

# 0. pip install python-dotenv openai
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")
if not openai.api_key:
    print("❌ set OPENAI_API_KEY in your .env")
    exit(1)

#
# 1) CREATE the Eval (just the schema + testing criteria)
#
# We tell it: each example has two fields, `prompt` (string) and `completion` (string).
schema = {
    "type": "object",
    "properties": {
        "prompt":     {"type": "string"},
        "completion": {"type": "string"}
    },
    "required": ["prompt", "completion"]
}

eval_config = openai.evals.create(
    name="quickstart-python",
    data_source_config={
        "type":        "custom",       # can also be "stored_completions"
        "item_schema": schema,
    },
    testing_criteria=[{"type": "string_match"}]  # use the built-in exact‐match grader
)

eval_id = eval_config["id"]
print("✅ Created Eval:", eval_id)


#
# 2) SUBMIT A RUN (your actual data) for grading
#
# Here we send one item inline.  You can send dozens this way.
run_items = [
    {
        "prompt":     "What is 2 + 2?",
        "completion": "4"
    }
]

run_response = openai.evals.runs.create(
    eval_id=eval_id,
    name="run-1",
    data_source={
        "type": "jsonl",
        "source": {
            "type":  "inline",    # valid values here are "inline" or "file_content"
            "items": run_items
        }
    }
)

print("✅ Created Run:", run_response["id"])
print("▶️ View report:", run_response["report_url"])
