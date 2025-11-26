
"""
Project: Joey - The Voice-Enabled End-to-End Career Copilot
File: agents/summarize_agent.py
Description: 
    Defines the factory for the 'Summarize Agent'.
    
    Role in Architecture:
    This is the [Step 1] Agent in the Sequential Pipeline.
    It acts as the "Analyst". It takes the raw, noisy Job Description (JD) text 
    and extracts a clean, structured JSON object containing:
    1. Metadata (Title, Company)
    2. Key Requirements (Hard/Soft Skills)
    3. Evidence Sentences (Context for the Writer)
"""

from google.adk.agents import Agent

def create_summarizer(model, instruction: str) -> Agent:
    """
    Constructs the Summarize Agent.
    
    Args:
        model: The Gemini model instance.
        instruction: The system prompt defining the extraction rules and JSON schema.
                     (See 'summarize_instruction' in instruction.txt)
                     
    Returns:
        Agent: An ADK Agent configured to output structured analysis.
    """
    return Agent(
        name="SummarizeAgent",
        model=model,
        instruction=instruction,
        
        # [Data Flow Strategy]
        # The output of this agent is NOT text for the user, but a data object.
        # We store it in the Session State under the key 'jd_analysis'.
        # The next agent (Finding Agent) and the Writer Agent will both read from this key.
        output_key="jd_analysis" 
    )