import os
import logging
from utils.file_handler import FileLoader

class BaseInterviewService:
    """
    公共基类：负责面试相关的上下文检索、文件查找和数据加载。
    """
    def __init__(self, jd_dir, cv_dir, repo_path):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.jd_dir = jd_dir
        self.cv_dir = cv_dir
        self.repo_path = repo_path
        self.file_loader = FileLoader()
        
        self.repo_content = self.file_loader.load(repo_path) or "(Skill Repo is empty)"

    def _find_file_fuzzy(self, directory, keywords, prefix_filter=None):
        """
        [Smart Fuzzy Search]
        Locates a file matching keywords, ignoring case and stop words.
        """
        if not os.path.exists(directory): return None
        
        # 1. 定义停用词 (Stop Words)
        STOP_WORDS = {'and', 'or', 'with', 'for', 'at', 'the', 'in', 'a', 'an', 'job', 'role', 'position', 'of'}
        
        # 2. 处理关键词：字符串 -> 列表 -> 过滤停用词
        if isinstance(keywords, str):
            # 将非字母数字字符替换为空格（除了点号保留作为扩展名判断）
            # 这里简单按空格分割即可
            raw_tokens = keywords.split()
        else:
            raw_tokens = keywords

        valid_keywords = [
            k.lower().strip() 
            for k in raw_tokens 
            if k.lower().strip() not in STOP_WORDS
        ]

        if not valid_keywords: return None
        
        # self.logger.debug(f"Searching in {os.path.basename(directory)} with tokens: {valid_keywords}")

        candidates = []
        for f in os.listdir(directory):
            if f.startswith('.'): continue
            f_lower = f.lower()
            
            # 3. 前缀检查 (大小写不敏感)
            if prefix_filter:
                if not f_lower.startswith(prefix_filter.lower()):
                    continue
            
            # 4. 关键词全匹配 (AND Logic)
            # 文件名必须包含所有 valid_keywords
            if all(vk in f_lower for vk in valid_keywords):
                candidates.append(os.path.join(directory, f))
        
        if candidates:
            # 返回最新的
            candidates.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            return candidates[0]
        return None

    def get_context_materials(self, keyword_str):
        """
        [核心逻辑] 获取所有面试相关的上下文材料
        """
        materials = {
            "company": "Target Company",
            "jd_text": "",          
            "cv_text": "",
            "resume_text": "",
            "debug_requirements": "", 
            "ready": False
        }

        # 1. Find JD
        jd_path = self._find_file_fuzzy(self.jd_dir, keyword_str)
        if jd_path: 
            materials["jd_text"] = self.file_loader.load(jd_path)
            # 尝试从文件名推断 Company (假设格式 jd_source_title_company_...)
            # 这只是一个备选，后面会尝试从 CV/Debug 获取更准的
            try:
                parts = os.path.basename(jd_path).split('_')
                if len(parts) > 3: materials["company"] = parts[3]
            except: pass

        # 2. Find CV (Cover Letter)
        cv_path = self._find_file_fuzzy(self.cv_dir, keyword_str, prefix_filter="CoverLetter")
        if cv_path:
            materials["cv_text"] = self.file_loader.load(cv_path)
            try:
                parts = os.path.basename(cv_path).split('_')
                if len(parts) > 2: materials["company"] = parts[2]
            except: pass

        # 3. Find Resume
        res_path = self._find_file_fuzzy(self.cv_dir, keyword_str, prefix_filter="Resume")
        if not res_path:
            res_path = self._find_file_fuzzy(self.cv_dir, keyword_str, prefix_filter="PersonalSummary")
        if res_path: 
            materials["resume_text"] = self.file_loader.load(res_path)

        # 4. Find DEBUG JSON
        json_path = None
        if cv_path:
            base = os.path.basename(cv_path)
            json_name = base.replace("CoverLetter", "DEBUG").rsplit('.', 1)[0] + ".json"
            p = os.path.join(self.cv_dir, json_name)
            if os.path.exists(p): json_path = p
        
        if not json_path:
            json_path = self._find_file_fuzzy(self.cv_dir, keyword_str, prefix_filter="DEBUG")

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

        if materials["jd_text"] or materials["resume_text"]: 
            materials["ready"] = True
            
        return materials
        