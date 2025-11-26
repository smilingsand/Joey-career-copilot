"""
Project: Joey - The Voice-Enabled End-to-End Career Copilot
File: context/user_manager.py
Description: 
    This module acts as the "Single Source of Truth" for the User's data.
    
    Role in Architecture:
    It loads, parses, and holds the user's static assets:
    1. User Profile (JSON): Structured data like name, contact info, preferences.
    2. Skill Repository (Word/Text): Unstructured narrative of all past projects and skills.
    
    This data is injected into the General Agent's system prompt to ensure 
    consistent and hallucin-free responses about the candidate.
"""

import json
import os
import logging
# Import the specialized loader for the Skill Repository
from tools.skill_store import SkillStore  

logger = logging.getLogger("UserManager")

class UserManager:
    """
    Manages the lifecycle of user data.
    It provides a unified interface for Agents to access user context.
    """

    def __init__(self, profile_path="user_profile.json", repo_path="Skill_Repository/亮点总结.docx"):
        """
        Initialize the User Manager.
        
        Args:
            profile_path: Path to the structured JSON profile.
            repo_path: Path to the unstructured Skill Repository document.
        """
        self.profile_path = profile_path
        self.repo_path = repo_path
        
        # In-memory storage for loaded data
        self.profile_data = {}
        self.full_skill_text = ""
        
        # Hydrate data on initialization
        self.load_data()

    def load_data(self):
        """
        [Data Ingestion Layer]
        Reads files from disk and populates memory.
        Includes error handling for missing files to prevent crash on startup.
        """
        # 1. Load Structured Profile (JSON)
        if os.path.exists(self.profile_path):
            try:
                with open(self.profile_path, 'r', encoding='utf-8') as f:
                    self.profile_data = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load profile: {e}")
        else:
            logger.warning(f"User profile not found at {self.profile_path}")
        
        # 2. Load Unstructured Skill Repository (Docx/Text)
        # Uses SkillStore utility to handle parsing and formatting
        if os.path.exists(self.repo_path):
            store = SkillStore(self.repo_path)
            self.full_skill_text = store.get_formatted_repo_content()
        else:
            logger.warning(f"Skill Repo not found at {self.repo_path}")

    def get_system_context(self) -> str:
        """
        [Context Engineering]
        Constructs the "System Context String" for LLM Injection.
        
        This method formats the raw data into a clear, tagged string that is 
        inserted into the 'General Agent's' system instruction.
        This ensures the Agent always knows WHO it is representing.
        
        Returns:
            str: Formatted context string ready for prompt injection.
        """
        # Safely extract key fields
        basic = self.profile_data.get('basic_info', {})
        prefs = self.profile_data.get('preferences', {})
        
        context = f"""
=== USER PROFILE ===
Name: {basic.get('name', 'User')}
Current Role: {basic.get('current_role', 'N/A')}
Experience: {basic.get('experience_years', 'N/A')} years
Preferences: {json.dumps(prefs, indent=2, ensure_ascii=False)}
Goals: {self.profile_data.get('career_goals', 'N/A')}

=== DETAILED SKILL REPOSITORY ===
{self.full_skill_text} 
"""
        return context