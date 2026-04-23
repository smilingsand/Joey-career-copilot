"""
Project: Joey - The Voice-Enabled End-to-End Career Copilot
File: services/avatar_service.py
Description: 
    The 'Body' for the Avatar Mode (Richard).
    Inherits from BaseInterviewService to access user knowledge.
    Manages the Microphone -> LLM -> Speaker loop for real-world interviews.
"""

import logging
import asyncio
from services.base_interview_service import BaseInterviewService

logger = logging.getLogger("AvatarService")

class AvatarService(BaseInterviewService):
    def __init__(self, jd_dir, cv_dir, repo_path, voice_service, prompts):
        """
        Initialize Richard.
        Args:
            jd_dir, cv_dir, repo_path: Passed to BaseInterviewService for file access.
            voice_service: The ears and mouth.
            prompts: Dictionary containing 'avatar' instruction.
        """
        # 1. 初始化父类 (获取 get_context_materials 和 repo_content 能力)
        super().__init__(jd_dir, cv_dir, repo_path)
        
        self.voice = voice_service
        self.prompts = prompts
        self.history = [] # Short-term conversation history

    def generate_answer_prompt(self, question, materials):
        """
        生成【Richard 替身】回答指令
        (逻辑与 Tom 类似，但使用 avatar_instruction 模板)
        """
        template = self.prompts['avatar']
        
        # 组装上下文 (复用 materials 里的数据)
        context_str = ""
        
        # A. 考点与背景
        if materials.get('debug_requirements'):
             context_str += f"\n=== TARGET REQUIREMENTS ===\n{materials['debug_requirements']}\n"
        
        if materials.get('jd_text'):
             context_str += f"\n=== JOB DESCRIPTION ===\n{materials['jd_text'][:2000]}...\n"

        # B1. 提交的COver Letter        
        if materials.get('cv_text'):
             context_str += f"\n=== MY COVER LETTER ===\n{materials['cv_text'][:2000]}\n"

        # B2. 提交的简历
        if materials.get('resume_text'):
             context_str += f"\n=== MY RESUME ===\n{materials['resume_text'][:2000]}\n"


        # C. 替换模板变量
        # {repo_content} 来自父类 BaseInterviewService
        final_prompt = template.replace("{repo_content}", self.repo_content) \
                               .replace("{context_materials}", context_str) \
                               .replace("{question}", question)
        
        return final_prompt

    async def run_avatar_session(self, target_job, llm_generator_func, log_callback=None):
        """
        启动 Richard 的主循环：听 -> 想 -> 说
        """
        print(f"\n[System] 🕴️ Richard (Avatar) is ONLINE. Loading context for '{target_job}'...")
        
        # 1. 调用父类方法获取材料
        materials = self.get_context_materials(target_job)
        
        if not materials["ready"]:
            return f"Error: Materials not found for '{target_job}'. Please ensure JD and Resume exist."
        
        # 清空历史
        self.history = []
        
        print(f"\n>>> RICHARD IS READY. Waiting for interviewer (Real Human) to speak... <<<")
        print("(Say 'Richard stop' or press Ctrl+C to end session)\n")

        # 2. 交互循环
        while True:
            try:
                # --- A. Listen (Human) ---
                print("\n[Richard] 👂 Listening...")
                interviewer_text = self.voice.listen()
                
                if not interviewer_text:
                    continue 
                
                print(f"\nInterviewer (Heard) > {interviewer_text}")
                
                # [新增] 记录面试官的话
                if log_callback:
                    log_callback("Interviewer (Human)", interviewer_text)

                # Exit Check
                if "richard stop" in interviewer_text.lower() or "stop simulation" in interviewer_text.lower():
                    print("[System] Stop command received. Richard signing off.")
                    break

                # --- B. Think (Brain) ---
                print(f"[System] 🧠 Richard is thinking...")
                
                # 拼接短期记忆
                context_history_str = ""
                if self.history:
                    recent_turns = self.history[-6:] 
                    context_history_str = "\n[Previous Conversation]:\n" + "\n".join([f"{r}: {t}" for r, t in recent_turns]) + "\n"
                
                augmented_question = f"{context_history_str}\n[Current Question]: {interviewer_text}"
                
                # 生成 Prompt (使用自己的方法)
                prompt = self.generate_answer_prompt(augmented_question, materials)
                
                # 调用 LLM
                response_text = await llm_generator_func(prompt)
                
                # --- C. Speak (Richard) ---
                print(f"\nRichard (Voice) > {response_text}")
                
                # [新增] 记录 Richard 的话
                if log_callback:
                    log_callback("Richard (Avatar)", response_text)

                self.history.append(("Interviewer", interviewer_text))
                self.history.append(("Richard", response_text))
                
                # 使用 'richard' 声音
                await self.voice.speak(response_text, persona="richard")
                
            except KeyboardInterrupt:
                print("\n[System] Manual interruption.")
                break
            except Exception as e:
                logger.error(f"Avatar Loop Error: {e}")
                await asyncio.sleep(1)

        return "Avatar session ended."

