"""
Project: Joey - The Voice-Enabled End-to-End Career Copilot
File: agents/finding_agent.py
Description: 
    Defines the factory for the 'Finding Agent'.
    This agent acts as the RAG (Retrieval-Augmented Generation) engine.
    
    Key Architecture Decision:
    Instead of using vector database retrieval tools, this agent uses "Long Context Injection".
    The user's full Skill Repository is injected directly into the system prompt, allowing
"""

from google.adk.agents import Agent

def create_finder(model, instruction: str) -> Agent:
    """
    Constructs the Finding Agent.
    
    Args:
        model: The Gemini model instance.
        instruction: The system prompt. 
                     CRITICAL: This string must already contain the injected '{skill_database}' content
                     before being passed here.
                     
    Returns:
        Agent: An agent configured to analyze requirements and output 'finding_results'.
    """
    # [Modification Note] 
    # Removed 'search_func' parameter. We now rely purely on instruction-based retrieval.
    return Agent(
        name="FindingAgent",
        model=model,
        instruction=instruction,
        # [修改] 移除 tools 列表，因为我们现在使用 Context 阅读模式
        # tools=[], 
        output_key="finding_results"
    )