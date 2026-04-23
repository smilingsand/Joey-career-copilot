"""
Project: Joey - The Voice-Enabled End-to-End Career Copilot
File: services/interview_copilot_service.py
Description: 
    The 'Brain' for the Real-time Assist feature (Joey).
    It leverages the BaseInterviewService to access context but uses a specific 
    'Copilot' persona prompt to generate strategic advice during an interview.
"""

import logging
from services.base_interview_service import BaseInterviewService

logger = logging.getLogger("InterviewCopilotService")

class InterviewCopilotService(BaseInterviewService):
    def __init__(self, jd_dir, cv_dir, repo_path, prompts):
        """
        Initialize Copilot Service.
        Inherits file finding and data loading capabilities from BaseInterviewService.
        """
        # 调用父类初始化 (复用查找逻辑和 Skill Repo 加载)
        super().__init__(jd_dir, cv_dir, repo_path)
        self.prompts = prompts
        
        logger.info("InterviewCopilotService (Joey) initialized.")

    def generate_answer_prompt(self, question, materials):
        """
        生成【Copilot 助攻】指令
        
        Args:
            question: 面试官的问题
            materials: 包含 JD, CV, Resume, Requirements 的字典
        """
        template = self.prompts['copilot']
        
        # ====================================================
        # 1. 构建职位要求上下文 (Job Requirements Context)
        # ====================================================
        # [修复] 读取Debug文件中的requirements section 和 JD 原文
        job_context_str = ""
        
        if materials.get('debug_requirements'):
             job_context_str += f"=== KEY REQUIREMENTS (Cheat Sheet) ===\n{materials['debug_requirements']}\n\n"
        
        if materials.get('jd_text'):
             # 截取前 1500 字符，提供公司介绍和上下文
             job_context_str += f"=== FULL JOB DESCRIPTION SNIPPET ===\n{materials['jd_text'][:1500]}...\n"
        
        if not job_context_str:
             job_context_str = "N/A (Job details missing)"


        # ====================================================
        # 2. 构建候选人提交材料上下文 (Submitted Materials)
        # ====================================================
        submitted_str = ""
        
        # submitted materials including Cover Letter & Resume，确保 Copilot 知道我们在求职信里吹过什么牛
        if materials.get('cv_text'):
             submitted_str += f"\n--- COVER LETTER ---\n{materials['cv_text'][:1500]}\n"

        if materials.get('resume_text'):
             submitted_str += f"\n--- RESUME ---\n{materials['resume_text'][:3000]}\n"
        else:
             submitted_str += "\n(Resume missing)\n"

        # ====================================================
        # 3. 注入 Prompt
        # ====================================================
        # 替换模板变量
        # {repo_content}: 来自父类 self.repo_content (技能库/第二大脑)
        # {debug_requirements}: 这里我们传入构建好的 job_context_str
        # {resume_text}: 这里我们传入整合好的 submitted_str
        
        prompt = template.replace("{company_name}", materials['company']) \
                         .replace("{question}", question) \
                         .replace("{repo_content}", self.repo_content) \
                         .replace("{debug_requirements}", job_context_str) \
                         .replace("{resume_text}", submitted_str)
                         
        return prompt