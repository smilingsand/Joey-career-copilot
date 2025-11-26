"""
Project: Joey - The Voice-Enabled End-to-End Career Copilot
File: agents/cv_pipeline.py
Description: 
    This module acts as the "Agent Factory". It assembles the individual sub-agents 
    into a sophisticated orchestration workflow.
    
    Architectural Pattern:
    Sequential Pipeline:
      1. Summarize Agent: Analyzes JD to find key requirements.
      2. Finding Agent (RAG): Searches the user's Skill Repo for matching evidence.
      3. Writer Agent: Synthesizes the CV/Cover Letter draft.
      4. Quality Control Loop:
         - Validator Agent: Critiques the draft against the JD.
         - Refiner Agent: Improves the draft based on feedback.
         (Repeats until approved or max iterations reached)
"""

from google.adk.agents import SequentialAgent, LoopAgent 
from google.adk.models.google_llm import Gemini
from google.genai import types

# --- Import Sub-Agent Factories ---
from .summarize_agent import create_summarizer
from .finding_agent import create_finder
from .writer_agent import create_writer
from .validator_agent import create_validator 
from .refiner_agent import create_refiner     

# ==========================================
# Configuration
# ==========================================
# Robust retry configuration to handle potential API rate limits (429) or service errors (503)
# essential for long-running multi-agent chains.
RETRY_CONFIG = types.HttpRetryOptions(
    attempts=5,
    exp_base=2,
    initial_delay=5,
    http_status_codes=[429, 500, 503, 504],
)

def create_cv_pipeline(model_name: str, full_repo_text: str, prompts: dict, max_iterations: int = 3):
    """
    Constructs the end-to-end CV Generation Pipeline.
    
    Args:
        model_name: The Gemini model to power all agents (e.g., "gemini-2.0-flash-001").
        full_repo_text: The user's full career history (Knowledge Base for RAG).
        prompts: Dictionary containing system instructions for each agent.
        max_iterations: Safety limit for the Validation/Refinement loop.
        
    Returns:
        Agent: A composite Agent (SequentialAgent) ready for execution.
    """
    # Initialize the shared model instance with retry logic
    model = Gemini(model=model_name, retry_options=RETRY_CONFIG)

    # --- Step 1: The Analyst ---
    # Extracts metadata and key requirements from the raw Job Description.
    summarizer = create_summarizer(model, prompts['summarize'])
    
    # --- Step 2: The Researcher (RAG) ---
    # Injects the huge Skill Repo into the prompt context.
    # Finds the best matching stories/skills for the requirements found in Step 1.
    raw_finding_prompt = prompts['finding']
    filled_finding_prompt = raw_finding_prompt.replace("{skill_database}", full_repo_text)
    finder = create_finder(model, filled_finding_prompt)
    
    # --- Step 3: The Author ---
    # Drafts the initial document based on requirements and found skills.
    writer = create_writer(model, prompts['writer'])

    # --- Step 4: The Quality Assurance Loop ---
    # This sub-system ensures high quality outputs through iterative refinement.
    
    validator = create_validator(model, prompts['validator'])
    refiner = create_refiner(model, prompts['refiner'])

    # Loop Logic: Validator critiques -> Refiner fixes -> Validator critiques...
    # Terminates when Validator says "APPROVED" (handled by internal logic) 
    # or when max_iterations is hit (hard limit).
    validation_loop = LoopAgent(
        name="QualityControlLoop",
        sub_agents=[validator, refiner],
        max_iterations=max_iterations
    )

    # --- Final Assembly ---
    # Chains all steps into a single, linear workflow.
    pipeline = SequentialAgent(
        name="ResumePipeline",
        sub_agents=[
            summarizer,     # Input: Raw JD -> Output: Requirements JSON
            finder,         # Input: Requirements -> Output: Matched Skills
            writer,         # Input: Skills -> Output: Draft V1
            validation_loop # Input: Draft V1 -> Output: Final Polished Draft
        ]
    )
    
    return pipeline
    