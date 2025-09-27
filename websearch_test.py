import os
import asyncio
import nest_asyncio
nest_asyncio.apply()
from agents import Agent, Runner, WebSearchTool

async def test_agent_with_web_search():
    # Set your OpenAI API key (ensure this is done before any agent initialization)
    os.environ["OPENAI_API_KEY"] = "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"

    # Specify the agent ID
    agent_id = "asst_SQi9qtibh5QNdVaZaNz95Ugn"

    # Create the agent
    agent = Agent(agent_id)

    # Initialize the WebSearchTool
    web_search_tool = WebSearchTool()

    # Create a Runner
    runner = Runner()
    runner.agent = agent
    runner.tools = [web_search_tool]

    # Define a query that might require web searching
    query = "What is the ViantAI Overview?"

    try:
        # Run the agent with the query
        response = await runner.run(
            input=query, 
            starting_agent=agent
        )

        # Print the response
        print("Agent Response:", response)

    except Exception as e:
        print(f"An error occurred: {e}")

# Run the async function
async def main():
    await test_agent_with_web_search()

# Execute the main function
if __name__ == "__main__":
    asyncio.run(main())