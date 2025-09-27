import openai

# Set with your OpenAI API key
openai.api_key = "sk-*******"
# Set your specific agent's model ID 
agent_model = "asst_*********"

def run_chat():
    # Initialize the conversation with a system message
    messages = [
        {"role": "system", "content": "You are a helpful assistant."}
    ]
    
    print("Chat session started. Type 'quit' to exit.")
    
    while True:
        user_input = input("User: ")
        if user_input.lower() in ("quit", "exit"):
            break

        # Add the user message to the conversation history
        messages.append({"role": "user", "content": user_input})

        try:
            # Send the entire conversation to the specified agent model
            response = openai.ChatCompletion.create(
                model=agent_model,
                messages=messages
            )
        except Exception as e:
            print("Error:", e)
            continue

        # Extract and clean the assistant's reply
        assistant_reply = response.choices[0].message.content.strip()
        messages.append({"role": "assistant", "content": assistant_reply})

        # Print the agent's reply after every change
        print("Agent:", assistant_reply)

if __name__ == "__main__":
    run_chat()
