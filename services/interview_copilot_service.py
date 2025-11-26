import os
import logging
import json
from utils.file_handler import FileLoader

logger = logging.getLogger("InterviewCopilotService")

class InterviewCopilotService:
    # [修改] 增加 prompts 参数
    def __init__(self, jd_dir, cv_dir, repo_path, prompts):
        self.jd_dir = jd_dir
        self.cv_dir = cv_dir
        self.repo_path = repo_path
        self.prompts = prompts # [新增] 接收配置
        self.file_loader = FileLoader()
        
        self.repo_content = self.file_loader.load(repo_path) or "(Skill Repo is empty)"

    def _find_file_fuzzy(self, directory, keywords, prefix_filter=None):
        """[复用] 模糊搜索文件逻辑"""
        if not os.path.exists(directory): return None
        
        STOP_WORDS = {'and', 'or', 'with', 'for', 'at', 'the', 'in', 'a', 'an', 'job', 'role', 'position'}
        valid_keywords = [k.lower().strip() for k in keywords if k and k.strip().lower() not in STOP_WORDS]
        if not valid_keywords: return None

        candidates = []
        for f in os.listdir(directory):
            if f.startswith('.'): continue
            f_lower = f.lower()
            if prefix_filter and not f_lower.startswith(prefix_filter.lower()): continue
            if all(vk in f_lower for vk in valid_keywords):
                candidates.append(os.path.join(directory, f))
        
        if candidates:
            candidates.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            return candidates[0]
        return None

    def get_context_materials(self, keyword_str):
        """获取上下文材料"""
        if isinstance(keyword_str, list): keywords = keyword_str
        else: 
            clean_str = keyword_str.replace(",", " ").replace(".", " ")
            keywords = clean_str.split()

        materials = {
            "company": "Target Company",
            "jd_text": "",
            "resume_text": "",
            "debug_requirements": "N/A",
            "debug_findings": "",
            "ready": False
        }

        # 1. Find JD
        jd_path = self._find_file_fuzzy(self.jd_dir, keywords)
        if jd_path: materials["jd_text"] = self.file_loader.load(jd_path)

        # 2. Find CV
        cv_path = self._find_file_fuzzy(self.cv_dir, keywords, prefix_filter="CoverLetter")
        if cv_path:
            try:
                parts = os.path.basename(cv_path).split('_')
                if len(parts) > 2: materials["company"] = parts[2]
            except: pass

        # 3. Find Resume
        res_path = self._find_file_fuzzy(self.cv_dir, keywords, prefix_filter="Resume")
        if not res_path:
            res_path = self._find_file_fuzzy(self.cv_dir, keywords, prefix_filter="PersonalSummary")
        if res_path: materials["resume_text"] = self.file_loader.load(res_path)

        # 4. Find DEBUG JSON
        json_path = None
        if cv_path:
            base = os.path.basename(cv_path)
            json_name = base.replace("CoverLetter", "DEBUG").rsplit('.', 1)[0] + ".json"
            p = os.path.join(self.cv_dir, json_name)
            if os.path.exists(p): json_path = p
        
        if not json_path:
            json_path = self._find_file_fuzzy(self.cv_dir, keywords, prefix_filter="DEBUG")

        if json_path:
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    reqs = data.get('requirements', [])
                    if reqs:
                        materials["debug_requirements"] = "\n".join([f"- {r}" for r in reqs])
                    if data.get('metadata', {}).get('company'):
                        materials["company"] = data['metadata']['company']
            except: pass

        if materials["jd_text"]: materials["ready"] = True
        return materials

    def generate_answer_prompt(self, question, materials):
        """
        [修改] 使用外部传入的模板生成指令
        """
        template = self.prompts['copilot']
        
        # 简单的防空处理
        res_text = materials.get('resume_text', '')[:3000] if materials.get('resume_text') else "(Resume missing)"
        
        # 替换模板变量
        # 注意：repo_content 已经在 __init__ 里加载了
        # copilot_name 已经在 app.py 加载模板时注入了
        
        prompt = template.replace("{company_name}", materials['company']) \
                         .replace("{question}", question) \
                         .replace("{repo_content}", self.repo_content) \
                         .replace("{debug_requirements}", materials.get('debug_requirements', 'N/A')) \
                         .replace("{resume_text}", res_text)
                         
        return prompt

        