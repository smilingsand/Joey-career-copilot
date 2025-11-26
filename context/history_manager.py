"""
Project: Joey - The Voice-Enabled End-to-End Career Copilot
File: context/history_manager.py
Description: 
    This module implements the "Long-Term Memory" component of the agent.
    
    Key Capabilities:
    1. Persistence: Saves conversation history to a local JSON file, allowing the agent 
       to "remember" past interactions across different application runs.
    2. Context Window Management: Dynamically slices the history to fit within the 
       LLM's token limit (e.g., only sending the last 30 turns).
    3. State Restoration: Reloads previous context upon startup.
"""

import json
import os
import logging
from datetime import datetime

logger = logging.getLogger("HistoryManager")

class HistoryManager:
    """
    Manages the storage, retrieval, and formatting of conversation history.
    Acts as a persistent memory layer sitting on top of the transient InMemorySessionService.
    """

    def __init__(self, session_dir, session_id, context_window=30):
        """
        Initialize the History Manager.

        Args:
            session_dir: Directory to store JSON history files.
            session_id: Unique identifier for the current session (or user).
            context_window: Maximum number of recent turns to inject into the LLM prompt 
                            (Preventing Context Window Overflow).
        """
        self.session_dir = session_dir
        self.session_id = session_id
        self.context_window = int(context_window)
        
        # Ensure storage persistence layer exists
        os.makedirs(self.session_dir, exist_ok=True)
        
        # Construct the persistence file path.
        # Using a fixed ID ensures "Continuity of Self" across restarts.
        self.file_path = os.path.join(self.session_dir, f"chat_history_{self.session_id}.json")
        
        # Hydrate memory from disk immediately upon initialization
        self.history = self._load_history_from_disk()

    def _load_history_from_disk(self):
        """
        [Persistence Layer] 
        Loads existing conversation turns from the JSON file.
        Returns an empty list if no history exists (Cold Start).
        """
        if not os.path.exists(self.file_path):
            return []
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load history: {e}")
            return []

    def add_turn(self, user_text, agent_text):
        """
        [State Mutation]
        Records a complete interaction cycle (User Input + Agent Response).
        Persists to disk immediately to prevent data loss on crash.
        """
        timestamp = datetime.now().isoformat()
        turn = {
            "timestamp": timestamp,
            "user": user_text,
            "agent": agent_text
        }
        self.history.append(turn)
        self._save_to_disk()

    def _save_to_disk(self):
        """Writes the current memory state to the JSON file."""
        try:
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(self.history, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save history: {e}")

    def get_context_string(self):
        """
        [Context Engineering]
        Formats the history into a structured string for Prompt Injection.
        
        Critical Logic:
        - Implements 'Sliding Window' mechanism.
        - Slices the list to include only the last N turns (`self.context_window`).
        - Prevents blowing up the LLM's token budget with too much history.
        """
        # Sliding Window: Take the last N turns
        recent_turns = self.history[-self.context_window:] if self.history else []
        
        if not recent_turns:
            return "No previous conversation."

        # Format as a dialogue script
        context_lines = []
        for turn in recent_turns:
            context_lines.append(f"User: {turn['user']}")
            context_lines.append(f"Agent: {turn['agent']}")
            context_lines.append("---")
        
        return "\n".join(context_lines)
        