"""
Project: Joey - The Voice-Enabled End-to-End Career Copilot
File: tools/skill_store.py
Description: 
    This module handles the "Knowledge Ingestion" phase for the RAG system.
    
    It is responsible for:
    1. Reading the user's "Skill Repository" (a Word document containing career stories).
    2. Parsing the unstructured text into a semi-structured dictionary using heuristics.
    3. Formatting this data into a token-efficient string for LLM Context Injection.
"""

import os
import logging
from docx import Document

class SkillStore:
    """
    A wrapper around the user's static knowledge base (Skill Repository).
    It acts as a read-only interface for Agents to access candidate details.
    """

    def __init__(self, repo_path):
        """
        Initialize the store and load data immediately.
        
        Args:
            repo_path: File path to the .docx file (e.g., 'Skill_Repository/Master_Resume.docx').
        """
        self.logger = logging.getLogger("SkillStore")
        self.repo_path = repo_path
        
        # Internal storage: Maps 'Category/Header' to 'List of Bullet Points/Paragraphs'
        self.skill_index = {} 
        
        # Hydrate upon initialization
        self._load_repo()

    def _load_repo(self):
        """
        [Parsing Logic]
        Iterates through the Word document paragraphs to build an in-memory index.
        
        Heuristic Strategy:
        Since the input is a standard Word doc, we use heuristics to distinguish 
        headers (Keys) from content (Values):
        1. Style Check: If style is 'Heading X', treat as a new category.
        2. Length Check: Short lines without ending punctuation are treated as implicit headers.
        """
        if not os.path.exists(self.repo_path):
            self.logger.warning(f"Repo not found at {self.repo_path}")
            return

        try:
            doc = Document(self.repo_path)
            current_key = "General" # Default bucket for uncategorized text
            self.skill_index[current_key] = []
            
            for para in doc.paragraphs:
                text = para.text.strip()
                if not text: continue # Skip empty lines
                
                # --- Heuristic Detection ---
                is_heading_style = para.style.name.startswith('Heading')
                # Assumption: Short lines (<50 chars) without periods are likely titles
                is_likely_title = len(text) < 50 and not text.endswith('.')
                
                if is_heading_style or is_likely_title:
                    # Switch context to new category
                    current_key = text
                    if current_key not in self.skill_index:
                        self.skill_index[current_key] = []
                else:
                    # Append content to current category
                    self.skill_index.setdefault(current_key, []).append(text)
            
            self.logger.info(f"Skill Repo loaded successfully: {len(self.skill_index)} categories found.")
            
        except Exception as e:
            self.logger.error(f"Failed to parse skill repository: {e}")

    def get_formatted_repo_content(self) -> str:
        """
        [Context Serialization]
        Converts the internal dictionary into a single, formatted string suitable for 
        injection into an LLM System Prompt.
        
        Format:
        === CANDIDATE SKILL DATABASE START ===
        ## SKILL CATEGORY: [Category Name]
        - [Experience Item 1]
        - [Experience Item 2]
        ...
        
        Why this format?
        Markdown-like headers help the LLM understand the semantic structure of the 
        candidate's experience, improving retrieval accuracy during RAG tasks.
        """
        output = []
        output.append("=== CANDIDATE SKILL DATABASE START ===")
        
        for category, paragraphs in self.skill_index.items():
            if not paragraphs: continue
            
            # Add Category Header
            output.append(f"\n## SKILL CATEGORY: {category}")
            
            # Add Content Items (Bullet points)
            for p in paragraphs:
                output.append(f"- {p}")
        
        output.append("\n=== CANDIDATE SKILL DATABASE END ===")
        
        return "\n".join(output)
        