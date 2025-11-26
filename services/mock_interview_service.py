"""
Project: Joey - The Voice-Enabled End-to-End Career Copilot
File: services/mock_interview_service.py
Description: 
    This service powers the "Mock Interview" feature.
    It is responsible for:
    1. Context Engineering: Automatically locating and loading the relevant Job Description, 
       Resume, Cover Letter, and Analysis files based on user keywords.
    2. Persona Generation: Constructing the "Hiring Manager" (Mary) system prompt with 
       strict knowledge boundaries (she only knows what's in the resume).
    3. Feedback Generation: Constructing the "Career Coach" (Joey) prompt for post-interview 
       reviews, injecting the full Skill Repository for "God Mode" analysis.
"""

import os
import logging
import json
from utils.file_handler import FileLoader

logger = logging.getLogger("MockInterviewService")

class MockInterviewService:
    """
    Service that orchestrates the context and prompts for the Mock Interview session.
    """

    def __init__(self, jd_dir, cv_dir, repo_path, prompts):
        """
        Initialize with paths and prompt templates.
        
        Args:
            jd_dir: Directory containing Job Descriptions.
            cv_dir: Directory containing generated CVs and Debug JSONs.
            repo_path: Path to the master Skill Repository.
            prompts: Dictionary of prompt templates (interviewer, coach).
        """
        self.jd_dir = jd_dir
        self.cv_dir = cv_dir
        self.repo_path = repo_path
        self.prompts = prompts
        self.file_loader = FileLoader()
        
        # Pre-load the Skill Repository into memory.
        # This is used ONLY for the "Review/Coach" phase, not the interview itself.
        self.repo_content = self.file_loader.load(repo_path) or "(Skill Repo is empty)"

    def _find_file_fuzzy(self, directory, keywords, prefix_filter=None):
        """
        [Smart Search Utility]
        Locates a file in a directory that matches a set of keywords.
        
        Logic:
        1. Filter out common stop words (e.g., 'and', 'for') to focus on unique entities (e.g., 'Reo', 'TM1').
        2. Check if ALL valid keywords exist in the filename (AND logic).
        3. If multiple matches found, return the most recently modified one.
        """
        if not os.path.exists(directory): return None
        
        # Stop words to ignore during filename matching
        STOP_WORDS = {'and', 'or', 'with', 'for', 'at', 'the', 'in', 'a', 'an', 'job', 'role', 'position'}
        candidates = []
        
        # Clean and tokenize keywords
        valid_keywords = [
            k.lower().strip() 
            for k in keywords 
            if k and k.strip().lower() not in STOP_WORDS
        ]

        if not valid_keywords: return None

        for f in os.listdir(directory):
            if f.startswith('.'): continue
            f_lower = f.lower()
            
            # Optional: Filter by file prefix (e.g., "CoverLetter")
            if prefix_filter and not f_lower.startswith(prefix_filter.lower()):
                continue
            
            # Match: Filename must contain ALL valid keywords
            if all(vk in f_lower for vk in valid_keywords):
                candidates.append(os.path.join(directory, f))
        
        # Sort by modification time (newest first) to handle multiple versions
        if candidates:
            candidates.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            return candidates[0]
        return None

    def get_interview_materials(self, keyword_str):
        """
        [Context Gathering]
        Orchestrates the retrieval of all necessary documents for an interview.
        
        Args:
            keyword_str: User input string (e.g., "Interview me for Reo Group").
        
        Returns:
            dict: A bundle containing file contents and metadata.
        """
        if isinstance(keyword_str, list):
            keywords = keyword_str
        else:
            # Simple tokenization
            clean_str = keyword_str.replace(",", " ").replace(".", " ")
            keywords = clean_str.split()

        materials = {
            "company": "Target Company",
            "jd_text": "",
            "jd_file": "Missing",
            "cv_text": "",
            "cv_file": "Missing",
            "resume_text": "",
            "resume_file": "Missing",
            "debug_file": "Missing", 
            "requirements": "",     # Extracted from DEBUG JSON
            "ready": False
        }

        # 1. Find Job Description (The Target)
        jd_path = self._find_file_fuzzy(self.jd_dir, keywords)
        if jd_path:
            materials["jd_file"] = os.path.basename(jd_path)
            materials["jd_text"] = self.file_loader.load(jd_path)

        # 2. Find Cover Letter (The Application)
        cv_path = self._find_file_fuzzy(self.cv_dir, keywords, prefix_filter="CoverLetter")
        if cv_path:
            materials["cv_file"] = os.path.basename(cv_path)
            materials["cv_text"] = self.file_loader.load(cv_path)
            try:
                # Infer company name from filename convention: CoverLetter_Title_Company_Date
                parts = os.path.basename(cv_path).split('_')
                if len(parts) > 2: materials["company"] = parts[2]
            except: pass

        # 3. Find Resume (The Candidate Profile)
        # Strategy: Look for 'Resume_*', fallback to 'PersonalSummary_*'
        res_path = self._find_file_fuzzy(self.cv_dir, keywords, prefix_filter="Resume")
        if not res_path:
            res_path = self._find_file_fuzzy(self.cv_dir, keywords, prefix_filter="PersonalSummary")
            
        if res_path:
            materials["resume_file"] = os.path.basename(res_path)
            materials["resume_text"] = self.file_loader.load(res_path)

        # 4. Find Debug Analysis (The Cheat Sheet)
        # This JSON contains the pre-analyzed 'Requirements' extracted by the Summarize Agent.
        json_path = None
        
        # Strategy 4.1: Infer JSON path from CV path (High precision)
        if cv_path:
            base = os.path.basename(cv_path)
            json_name = base.replace("CoverLetter", "DEBUG").rsplit('.', 1)[0] + ".json"
            p = os.path.join(self.cv_dir, json_name)
            if os.path.exists(p): json_path = p
        
        # Strategy 4.2: Fuzzy search if inference fails
        if not json_path:
            json_path = self._find_file_fuzzy(self.cv_dir, keywords, prefix_filter="DEBUG")

        if json_path:
            materials["debug_file"] = os.path.basename(json_path)
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Extract structural requirements for better prompting
                    reqs = data.get('requirements', [])
                    if reqs:
                        materials["requirements"] = "\n".join([f"- {r}" for r in reqs])
                    # Update company name if available in metadata
                    if data.get('metadata', {}).get('company'):
                        materials["company"] = data['metadata']['company']
            except: pass

        # Validation: We need at least a JD or a Resume to conduct an interview
        if materials["jd_text"] or materials["resume_text"]:
            materials["ready"] = True
            
        return materials

    def generate_system_prompt(self, materials):
        """
        [Persona Injection]
        Constructs the System Prompt for the 'Hiring Manager' persona.
        Crucially, this prompt only includes info the interviewer SHOULD know (JD + Resume).
        It does NOT include the full Skill Repo.
        """
        context_str = ""
        
        # Priority: Use pre-analyzed requirements if available, otherwise raw JD text
        if materials['requirements']:
            context_str += f"\n**JOB REQUIREMENTS (Analyzed from Debug File):**\n{materials['requirements']}\n"
        elif materials['jd_text']:
            context_str += f"\n**JOB DESCRIPTION:**\n{materials['jd_text'][:3000]}\n"
        else:
            context_str += "\n(Job Description is missing.)\n"
            
        if materials['resume_text']:
            context_str += f"\n**CANDIDATE RESUME:**\n{materials['resume_text']}\n"
        if materials['cv_text']:
            context_str += f"\n**CANDIDATE COVER LETTER:**\n{materials['cv_text']}\n"
        
        # Inject content into the template defined in instruction.txt
        template = self.prompts['interviewer']
        final_prompt = template.replace("{company_name}", materials['company']) \
                               .replace("{context_materials}", context_str)
        return final_prompt

    def generate_review_prompt(self):
        """
        [Feedback Mode]
        Constructs the System Prompt for the 'Career Coach' persona.
        This injects the 'Hidden Context' (Full Skill Repo) to provide gap analysis.
        """
        template = self.prompts['coach']
        # Inject the user's full history (God Mode) for the coach to see what was missed
        final_prompt = template.replace("{repo_content}", self.repo_content[:10000])
        return final_prompt

        