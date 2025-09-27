#!/usr/bin/env python3
import os
from dotenv import load_dotenv
from openai import OpenAI
from openai_agents import Agent, Runner
from openai_agents.tools.web_search import WebSearchTool

# 1) load creds
load_dotenv()  
API_KEY      = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")
if not API_KEY or not ASSISTANT_ID:
    raise RuntimeError("Please set OPENAI_API_KEY and ASSISTANT_ID in your .env")

# 2) build the typed client + your deployed Assistant
client = OpenAI(api_key=API_KEY)
agent  = Agent.from_assistant_id(client=client, assistant_id=ASSISTANT_ID)

# 3) wire in the same WebSearch tool your Assistant is configured with
runner = Runner(agent, WebSearchTool())

# 4) quick REPL
print("HelpSiteFAQ Agent (type exit/quit to stop)\n")
while True:
    q = input("❯ ").strip()
    if not q or q.lower() in ("exit","quit"):
        break
    print(runner.invoke(q), "\n")
