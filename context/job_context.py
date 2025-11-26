"""
Project: Joey - The Voice-Enabled End-to-End Career Copilot
File: context/job_context.py
Description: 
    This module defines the `JobContext` class, which acts as the "Shared State" 
    or "Blackboard" for the CV Generation Pipeline.
    
    Role in Architecture:
    As the Multi-Agent system executes (Summarize -> Find -> Write -> Validate), 
    intermediate results are not just passed as strings but accumulated here.
    This allows for:
    1. Structured Data Extraction: Parsing JSON from LLM outputs.
    2. State Persistence: Saving a snapshot of the entire reasoning process (DEBUG JSON).
    3. Error Handling: Validating if the pipeline produced usable output.
"""

import os
import logging
import json
import re
from datetime import datetime

logger = logging.getLogger("JobContext")

# ==========================================
# Data Context Object
# ==========================================
class JobContext:
    """
    A state container that accumulates data throughout the lifecycle of processing 
    a single Job Description file.
    """

    def __init__(self, file_path):
        """
        Initialize an empty context for a specific job file.
        """
        self.file_path = file_path
        self.file_name = os.path.basename(file_path)
        
        # --- State Variables ---
        self.job_metadata = {}          # From SummarizeAgent (Title, Company)
        self.requirements = []          # From SummarizeAgent (Key Skills)
        self.requirements_evidence = {} # From SummarizeAgent (Quotes from JD)
        self.findings = {}              # From FindingAgent (RAG Results)
        self.raw_writer_output = ""     # From WriterAgent (Full draft text)
        self.cover_letter_content = ""  # Parsed Cover Letter
        self.resume_summary_content = "" # Parsed Resume Summary
        self.validation_logs = []       # Trace of Validator-Refiner loops

    def parse_event_text(self, text: str, agent_name: str = "Unknown"):
        """
        [Event Handler]
        Parses streaming text chunks from the Agent Runner.
        Identifies which agent is speaking and extracts relevant structured data.
        """
        if not text or len(text.strip()) == 0: return

        # 1. Handle Validator Feedback (Quality Control Loop)
        if agent_name == "ValidatorAgent":
            clean_text = text.strip()
            # Deduplication logic: Prevent logging the same feedback twice if streamed in chunks
            if self.validation_logs:
                last_entry = self.validation_logs[-1]
                if last_entry["feedback"] == clean_text:
                    return # Skip duplicate
            
            logger.info(f"Captured Validation Feedback ({len(text)} chars)")
            self.validation_logs.append({
                "time": datetime.now().strftime("%H:%M:%S"),
                "feedback": clean_text
            })
            return 

        # 2. Handle Summarize Agent (JSON Extraction)
        # Look for specific keys that indicate the Summarizer's JSON output
        if '"job_metadata"' in text and '"requirements"' in text:
            parsed = self._safe_parse_json(text)
            if parsed:
                self.job_metadata = parsed.get("job_metadata", {})
                self.requirements = parsed.get("requirements", [])
                self.requirements_evidence = parsed.get("requirements_evidence", {}) 
                
                # Clean up strings
                if "title" in self.job_metadata: self.job_metadata["title"] = str(self.job_metadata["title"]).strip()
                if "company" in self.job_metadata: self.job_metadata["company"] = str(self.job_metadata["company"]).strip()

        # 3. Handle Finding Agent (RAG Results)
        if '"finding_output"' in text:
             parsed = self._safe_parse_json(text)
             if parsed and "finding_output" in parsed:
                 self.findings = parsed["finding_output"]

        # 4. Handle Writer / Refiner Agent (Document Generation)
        # Detect specific delimiters used in the Writer's system prompt
        if "=== COVER LETTER START ===" in text:
            self.raw_writer_output = text
            self._parse_writer_output()
        elif "ABORT_NO_MATERIALS" in text:
            self.raw_writer_output = "ABORTED"

    def _safe_parse_json(self, content):
        """
        [Robustness Utility]
        Extracts valid JSON objects from potentially noisy LLM text responses.
        Uses regex to find the first outer-most curly braces { ... }.
        """
        try:
            match = re.search(r"(\{.*\})", content, re.DOTALL)
            return json.loads(match.group(1)) if match else None
        except json.JSONDecodeError: return None

    def _parse_writer_output(self):
        """
        Splits the raw writer output into separate documents using delimiters.
        """
        if not self.raw_writer_output or self.raw_writer_output == "ABORTED": return
        
        # Extract Cover Letter
        cl_match = re.search(r"=== COVER LETTER START ===(.*?)=== COVER LETTER END ===", self.raw_writer_output, re.DOTALL)
        self.cover_letter_content = cl_match.group(1).strip() if cl_match else ""
        
        # Extract Summary
        sum_match = re.search(r"=== SUMMARY START ===(.*?)=== SUMMARY END ===", self.raw_writer_output, re.DOTALL)
        self.resume_summary_content = sum_match.group(1).strip() if sum_match else ""
    
    def is_valid_for_saving(self):
        """Validation check before writing to disk."""
        if not self.job_metadata: return False
        if not self.cover_letter_content or len(self.cover_letter_content) < 50: return False
        return True

    def save_debug_json(self, output_dir, base_name: str):
        """
        [Observability]
        Saves the entire context state to a JSON file.
        This is crucial for debugging and provides the 'Analysis' context for the Mock Interviewer.
        """
        final_name = f"DEBUG_{base_name}"
        debug_data = {
            "original_file": self.file_name,
            "metadata": self.job_metadata,
            "requirements": self.requirements,
            "requirements_evidence": self.requirements_evidence,
            "findings": self.findings,
            "validation_history": self.validation_logs, 
            # "raw_writer_output": self.raw_writer_output, # Optional: save raw output if needed
            "cover_letter_content": self.cover_letter_content, 
            "resume_summary_content": self.resume_summary_content
        }
        path = os.path.join(output_dir, f"{final_name}.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(debug_data, f, indent=2, ensure_ascii=False)
        except Exception: pass

        