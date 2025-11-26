"""
Project: Joey - The Voice-Enabled End-to-End Career Copilot
File: agents/validator_agent.py
Description: 
    Defines the factory for the 'Validator Agent'.
    
    Role in Architecture (Loop Pattern):
    This agent acts as the "Quality Assurance" (QA) layer or "Strict Hiring Manager".
    It sits inside the LoopAgent alongside the Refiner Agent.
    
    Its responsibility is to review the current draft against the JD requirements and decide:
    1. Is it good enough? -> Output "APPROVED".
    2. Does it need work? -> Output specific, actionable feedback for the Refiner.
"""

from google.adk.agents import Agent

def create_validator(model, instruction: str) -> Agent:
    """
    Constructs the Validator Agent.
    
    Args:
        model: The Gemini model instance.
        instruction: System prompt defining the evaluation checklist (STAR method, formatting, etc.)
                     and the success criteria.
                     
    Returns:
        Agent: An agent configured to output critique or approval signals.
    """
    return Agent(
        name="ValidatorAgent",
        model=model,
        instruction=instruction,
        
        # [Control Flow Strategy]
        # The output of this agent is stored in 'validation_feedback'.
        # This key drives the logic of the next agent (Refiner):
        # - If 'validation_feedback' == "APPROVED", the Refiner calls 'exit_loop()'.
        # - Otherwise, the Refiner uses this feedback to generate a new 'final_content'.
        output_key="validation_feedback" 
    )