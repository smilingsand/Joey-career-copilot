"""
Project: Joey - The Voice-Enabled End-to-End Career Copilot
File: agents/refiner_agent.py
Description: 
    Defines the 'Refiner Agent', the worker component of the Quality Control Loop.
    
    Role in Architecture (Loop Pattern):
    1. Input: Receives the current draft ('final_content') and the Validator's critique.
    2. Action: Either rewrites the draft to fix issues OR calls 'exit_loop' if approved.
    3. Output: Updates 'final_content' for the next iteration.
"""

from google.adk.agents import Agent
from google.adk.tools import FunctionTool

# ==========================================
# Loop Control Mechanism
# ==========================================
def exit_loop():
    """
    [Critical Tool] The Termination Signal.
    
    The LLM is instructed to call this tool ONLY when the input feedback is "APPROVED".
    When called, the ADK LoopAgent detects this and breaks the execution cycle,
    allowing the pipeline to proceed or finish.
    """
    return "Optimization Complete. Exiting loop."

def create_refiner(model, instruction: str) -> Agent:
    """
    Constructs the Refiner Agent.
    
    Args:
        model: The Gemini model instance.
        instruction: System prompt defining the Editor persona and formatting rules.
    """
    # Bind the exit logic as a tool the agent can wield
    exit_tool = FunctionTool(func=exit_loop)
    
    return Agent(
        name="RefinerAgent",
        model=model,
        instruction=instruction,
        tools=[exit_tool],
        
        # [State Management Strategy] 
        # The Refiner outputs to the SAME key ("final_content") that it reads from.
        # This overwrites the old draft with the new, improved version in the Session State.
        # This ensures the Validator (in the next step) always reviews the latest version.
        output_key="final_content" 
    )