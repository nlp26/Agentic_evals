#!/usr/bin/env python3
import openai
from agents.agent import Agent
from agents.extensions.visualization import draw_graph

# Replace these placeholders with your actual credentials.
API_KEY = "sk-************************"
ASSISTANT_ID = "asst_************************"

# Set the OpenAI API key globally.
openai.api_key = API_KEY

def main():
    """
    Test the Agent according to the docs:
      1. Instantiate the Agent with a required name.
      2. Configure the assistant ID.
      3. Submit a test prompt.
      4. Print the assistant's response.
      5. Visualize the conversation graph.
    """
    # Instantiate the Agent with the required name parameter.
    agent = Agent(name="Restaurant-V1")
    
    # Configure the Agent by setting the assistant_id attribute.
    agent.assistant_id = ASSISTANT_ID
    
    # Define a test prompt.
    prompt = "Hi Restaurant AI, what can you do for me?"
    print("User:", prompt)
    
    # Execute the agent with the test prompt.
    # This will internally create a conversation thread, send the message,
    # wait for the assistant's response, and log the conversation.
    assistant_reply = agent(prompt)
    print("Assistant:", assistant_reply)
    
    # Visualize the conversation as a graph.
    print("Drawing conversation graph...")
    draw_graph(agent=agent)

if __name__ == "__main__":
    main()




