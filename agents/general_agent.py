"""
Project: Joey - The Voice-Enabled End-to-End Career Copilot
File: agents/general_agent.py
Description: 
    Defines the factory for the 'General Advisor Agent'.
    
    Role in Architecture:
    This agent acts as the "Router" or "Central Hub". It is the first point of contact 
    for the user. It does not perform specialized tasks (like scraping or writing) itself.
    Instead, it uses its intelligence to:
    1. Understand User Intent (e.g., "Find jobs", "Practice interview").
    2. select the appropriate Tool from its toolkit.
    3. Maintain conversation history and user context.
"""

from google.adk.agents import Agent
from google.adk.models.google_llm import Gemini
from google.genai import types

def create_general_advisor(model_name: str, user_context: str, instruction_template: str, tools=None) -> Agent:
    """
    Constructs the General Advisor Agent (The Router).

    Args:
        model_name: The Gemini model version (e.g., "gemini-2.0-flash-001").
        user_context: A pre-formatted string containing the user's Profile and Skill Repo.
                      This gives the agent "Ground Truth" knowledge about the user.
        instruction_template: The raw system prompt template (from instruction.txt).
                              Must contain a '{user_context}' placeholder.
        tools: A list of ADK FunctionTools (e.g., [search_jobs_tool, start_interview_tool]).
               These are the "Spokes" that this Hub agent can call.

    Returns:
        Agent: A configured ADK Agent ready to run in the main loop.
    """
    
    # [Context Engineering] 
    # Dynamic Injection: We merge the static System Instructions with the dynamic User Profile.
    # This ensures the agent always knows WHO it is helping without needing RAG for basic facts.
    final_instruction = instruction_template.replace("{user_context}", user_context)
    
    # Initialize the LLM
    model = Gemini(model=model_name)
    
    return Agent(
        name="GeneralAdvisor",
        model=model,
        instruction=final_instruction,
        
        # [Hub-and-Spoke Architecture]
        # The 'tools' list connects this central brain to the specialized services.
        # Without tools, it's just a chatbot. With tools, it's an Agent.
        tools=tools or [] 
    )