"""
Project: Joey - The Voice-Enabled End-to-End Career Copilot
File: agents/writer_agent.py
Description: 
    Defines the factory for the 'Writer Agent'.
    
    Role in Architecture:
    This agent acts as the "Content Synthesizer" or "Professional Author".
    It sits at [Step 3] of the Sequential Pipeline.
    
    Input Context:
    1. 'jd_analysis' (from Summarize Agent): What the employer wants.
    2. 'finding_results' (from Finding Agent): What the candidate has.
    
    Output:
    Generates the first complete draft ('final_content') of the Cover Letter 
    and Resume Summary, ready for validation.
"""

from google.adk.agents import Agent

def create_writer(model, instruction: str) -> Agent:
    """
    Constructs the Writer Agent.
    
    Args:
        model: The Gemini model instance.
        instruction: System prompt defining the 'HR Specialist' persona,
                     style guidelines (No AI cliches), and formatting rules (STAR method).
                     
    Returns:
        Agent: An agent configured to produce the initial draft.
    """
    return Agent(
        name="WriterAgent",
        model=model,
        instruction=instruction,
        
        # [Data Flow Strategy]
        # The output is stored in 'final_content'.
        # This key is crucial because it acts as the "Shared Artifact" for the subsequent loop.
        # The Validator will read 'final_content', and the Refiner will overwrite 'final_content'.
        output_key="final_content"
    )