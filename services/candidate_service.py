"""
Project: Joey - The Voice-Enabled End-to-End Career Copilot
File: services/candidate_service.py
Description: 
    The 'Brain' for the AI Candidate (Tom).
    It leverages the BaseInterviewService to access context but uses a specific 
    'Candidate' persona prompt to generate spoken answers during auto-simulation.
"""

import logging
from services.base_interview_service import BaseInterviewService

logger = logging.getLogger("CandidateService")

class CandidateService(BaseInterviewService):
    def __init__(self, jd_dir, cv_dir, repo_path, prompts):
        """
        Initialize Candidate Service.
        Inherits file finding and data loading capabilities from BaseInterviewService.
        """
        # 1. 调用父类初始化 (复用查找逻辑和 Skill Repo 加载)
        super().__init__(jd_dir, cv_dir, repo_path)
        self.prompts = prompts
        
        logger.info("CandidateService (Tom) initialized.")

    def generate_answer_prompt(self, question, materials):
        """
        生成【Tom 候选人】回答指令
        
        Args:
            question: 面试官 (Mary) 的问题
            materials: 包含 JD, CV, Resume, Requirements 的字典
        """
        template = self.prompts['candidate']
        
        # 2. 组装上下文 (What Tom knows about the interview context)
        context_str = ""
        
        # A. 考试重点 (Target Requirements)
        if materials.get('debug_requirements'):
             context_str += f"\n=== TARGET REQUIREMENTS (Key Focus) ===\n{materials['debug_requirements']}\n"
        
        if materials.get('jd_text'):
             context_str += f"\n=== JOB DESCRIPTION CONTEXT ===\n{materials['jd_text'][:2000]}...\n"

        # B. Summitted materials, including Cover Letter and Resume
        if materials.get('cv_text'):
             context_str += f"\n=== MY COVER LETTER ===\n{materials['cv_text'][:1500]}\n"

        if materials.get('resume_text'):
             context_str += f"\n=== MY SUBMITTED RESUME ===\n{materials['resume_text'][:3000]}\n"

        # 3. 替换模板变量
        # {repo_content} 来自父类 self.repo_content (Tom 的核心记忆)
        final_prompt = template.replace("{repo_content}", self.repo_content) \
                               .replace("{context_materials}", context_str) \
                               .replace("{question}", question)
        
        return final_prompt

