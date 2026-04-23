"""
Project: Joey - The Voice-Enabled End-to-End Career Copilot
File: backend_core.py
Description: 
    [REFACTORED] This module contains the core initialization logic extracted from app.py.
    It sets up the Services, Agents, Tools, and State Management, but DOES NOT run the interaction loop.
    It returns a context dictionary to be used by UI frontends (CLI or Streamlit).
"""

import os
import logging
import asyncio
import uuid
import tomllib
import configparser
import re
import string
import json
import datetime # 确保导入 datetime
from dotenv import load_dotenv, find_dotenv

# --- Google Agents Development Kit (ADK) Imports ---
from google.adk.runners import Runner 
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool 
#import google.generativeai as genai    # deprecated SDK
from google import genai
from google.genai import types


# --- Local Context & Agent Modules ---
from context.user_manager import UserManager
from context.history_manager import HistoryManager 
from agents.general_agent import create_general_advisor

# --- Service Layer Imports (The Spokes) ---
from services.cv_maker_service import CVMakerService
from services.job_scout_service import JobScoutService     
from services.mock_interview_service import MockInterviewService
from services.interview_copilot_service import InterviewCopilotService
from services.voice_service import VoiceService
from services.candidate_service import CandidateService
from services.avatar_service import AvatarService # [新增]

# ==========================================
# Observability & Logging Configuration  - 3 level: logging.INFO, logging.WARNING, logging.ERROR
# ==========================================
# 1. Basic config：Keep WARNING level，capture normal system warning
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(message)s')

# 2. Set global level to ERROR to reduce noise from third-party libraries
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("httpcore").setLevel(logging.ERROR)
logging.getLogger("urllib3").setLevel(logging.ERROR)

# 3. Set Google SDK level to ERROR, filter warning message
logging.getLogger("google_genai").setLevel(logging.ERROR)
logging.getLogger("google_adk").setLevel(logging.ERROR)
# Legacy google.generativeai logger removed
logging.getLogger("common").setLevel(logging.ERROR) # some Google internal library use common logger
# [新增] 屏蔽 asyncio 的资源清理报错 (Unclosed client session 等)
# 将其设为 CRITICAL，意味着除非是致命错误，否则忽略普通的 ERROR 报错
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

# 4. Enable INFO logging for core business logic to trace Agent reasoning
logger = logging.getLogger("App")
logger.setLevel(logging.INFO)
logging.getLogger("JobScoutService").setLevel(logging.INFO)
logging.getLogger("MockInterviewService").setLevel(logging.INFO)
logging.getLogger("InterviewCopilotService").setLevel(logging.INFO)
logging.getLogger("CVMakerService").setLevel(logging.INFO)
logging.getLogger("CandidateService").setLevel(logging.INFO)
logging.getLogger("AvatarService").setLevel(logging.INFO)
logging.getLogger("VoiceService").setLevel(logging.INFO)


# ==========================================
# Configuration Helpers
# ==========================================
def load_settings(ini_path="settings.ini"):
    """Loads system configuration (API keys, paths, model names) from INI file."""
    if not os.path.exists(ini_path):
        logger.error(f"Settings file not found: {ini_path}")
        return None
    config = configparser.ConfigParser()
    config.read(ini_path, encoding='utf-8')
    return config

def load_instructions(file_path):
    """Loads system prompts and persona definitions from TOML file."""
    if not os.path.exists(file_path):
        logger.error(f"Instruction file not found: {file_path}")
        return None
    try:
        with open(file_path, "rb") as f:
            return tomllib.load(f)
    except Exception as e:
        logger.error(f"Failed to parse instructions: {e}")
        return None

def smart_find_file(user_query: str, directory: str) -> str:
    """
    [智能匹配升级版]
    根据用户输入的关键词在目录下寻找最匹配的文件。
    
    Improvements:
    1. 停用词过滤 (Stop Words Removal): 忽略 'role', 'job', 'cv' 等干扰词。
    2. 灵活分词: 将 'Analytics_KPMG' 拆分为 'analytics' 和 'kpmg'。
    """
    if not user_query or user_query.upper() == "ALL":
        return "ALL"
        
    if not os.path.exists(directory):
        return user_query 

    # 1. 定义干扰词 (这些词如果出现在查询中，会被忽略)
    STOP_WORDS = {
        'the', 'a', 'an', 'in', 'on', 'at', 'for', 'of', 'with', 'to',
        'job', 'role', 'position', 'opening', 'work',
        'cv', 'resume', 'cover', 'letter', 'application', 'generate', 'make', 'write', 'process', 'create',
        'please', 'find', 'search', 'file', 'md', 'docx'
    }

    # 2. 预处理用户输入
    # 将非字母数字字符替换为空格 (例如 "Analytics_KPMG" -> "Analytics KPMG")
    normalized_query = re.sub(r'[^a-zA-Z0-9]', ' ', user_query.lower())
    
    # 拆分并过滤停用词
    tokens = [t for t in normalized_query.split() if t not in STOP_WORDS]

    if not tokens:
        return "ALL" # 如果过滤完没词了(比如只说了 "generate cv")，默认为 ALL

    logger.info(f"Smart Find Tokens: {tokens}")

    candidates = []
    
    for filename in os.listdir(directory):
        if filename.startswith('.'): continue
        
        # 3. 预处理文件名 (同样去除非法字符)
        clean_filename = re.sub(r'[^a-z0-9]', '', filename.lower())
        
        # 4. 匹配逻辑：文件名必须包含所有有效 token
        # (AND 逻辑：必须同时包含 'forensic' 和 'kpmg')
        if all(token in clean_filename for token in tokens):
            candidates.append(filename)
            
    if not candidates:
        logger.warning(f"No fuzzy match found for '{user_query}'.")
        return user_query # 返回原值，让 Service 尝试自行处理或报错
        
    # 如果有多个匹配，返回最新的一个
    candidates.sort(key=lambda x: os.path.getmtime(os.path.join(directory, x)), reverse=True)
    best_match = candidates[0]
    
    logger.info(f"Fuzzy Match Success: '{user_query}' -> '{best_match}'")
    return best_match



# ==========================================
# Core Initialization Logic
# ==========================================
# [Changed] Renamed from main() to init_career_copilot()
# [Changed] Returns a dictionary of objects instead of running a loop
async def init_career_copilot():
    """
    Initializes the entire Career Copilot backend system.
    Returns a dictionary containing the Runner, Services, State, and Config.
    """

    # 1. Environment Setup
    env_file = find_dotenv()
    if env_file: load_dotenv(env_file, override=True)
    else: load_dotenv(override=True)
    
    # Validate critical API Keys
    if not os.getenv("GOOGLE_API_KEY"):
        logger.error("Google API Key missing.")
        return
    if not os.getenv("RAPIDAPI_KEY"):
        logger.error("RapidAPI Key missing.")
        return

    settings = load_settings()
    if not settings: return

    # 2. Configuration Extraction
    # Models & Paths
    model_name = settings['Model']['model_name']
    repo_dir = settings['Paths']['repo_dir']
    repo_file = settings['Paths']['repo_filename']
    profile_file = settings['Paths']['profile_filename']
    instruct_file = settings['Paths']['instruction_file']
    
    jd_dir = settings['Paths']['input_dir']
    cv_dir = settings['Paths']['export_dir']
    url_dir = settings['Paths']['url_dir']
    transcript_dir = settings['Paths'].get('transcript_dir', 'temp') # 默认为 'temp'
    
    # Workflow Settings
    max_iterations = int(settings['Workflow']['max_loop_iterations'])
    auto_interview_max_turns = int(settings['Workflow']['auto_interview_max_turns'])
    session_storage_dir = settings['Paths']['session_dir']
    context_window = int(settings['Memory']['context_window_turns'])
    enable_long_memory = settings.getboolean('Memory', 'enable_long_memory', fallback=True)

    default_engine = settings['Search']['default_engine']
    max_results = settings['Search']['max_results']
    
    # [新增] 读取 Transcript 配置，将字符串 "PREP, AUTO" 转换为集合 {'PREP', 'AUTO'} 以便快速查找
    raw_trans_modes = settings.get('Transcript', 'enabled_modes', fallback='')
    enabled_transcript_modes = {m.strip().upper() for m in raw_trans_modes.split(',') if m.strip()}

    # Persona Configuration (Decoupling names from code)
    copilot_name = settings.get('Personas', 'copilot_name', fallback='Joey')
    interviewer_name = settings.get('Personas', 'interviewer_name', fallback='Mary')
    candidate_name = settings.get('Personas', 'candidate_name', fallback='Tom')
    avatar_name = settings.get('Personas', 'avatar_name', fallback='Richard')
    settings_user_name = settings.get('Personas', 'user_name', fallback='')

    # [State Machine] Global App State
    # Used to handle complex interaction logic (e.g., preventing exit during interviews)
    app_state = {
        "is_interview_active": False,
        "current_job_keyword": None,
        "transcript_path": None # [新增] 记录当前面试的文字记录路径
    }

    # [Helper] Setup Transcript File
    def setup_transcript_file(mode: str, job_title: str, company: str) -> str:
        """
        统一生成 Transcript 文件路径。
        如果 mode 不在允许列表中，返回 None。
        Format: transcript_{MODE}_{Job}_{Company}_{Time}.txt
        """
        # 1. 检查权限
        if mode.upper() not in enabled_transcript_modes:
            return None

        # 2. 确保 temp 目录存在
        if not os.path.exists(transcript_dir):
                os.makedirs(transcript_dir)
            
        # 3. 清洗文件名 (去除非法字符，将空格转为下划线)
        def clean_name(n):
            return re.sub(r'[\\/*?:"<>|]', "", str(n)).replace(" ", "_")
        
        # 1. 预处理: 从 job_title 中移除 company (忽略大小写)
        # 例如: job="TM1 Developer at Reo Group", comp="Reo Group" -> job="TM1 Developer at"
        if company and company.lower() in job_title.lower():
            pattern = re.compile(re.escape(company), re.IGNORECASE)
            job_title = pattern.sub("", job_title)
            
        '''
        # 应该不会出现这类词，所以这段代码不需要
        # 2. 再次清洗无意义的介词 (at, for, in, of) 以保持文件名整洁
        # 例如: "TM1 Developer at" -> "TM1 Developer"
        for prep in [" at ", " for ", " in ", " of "]:
            pattern = re.compile(prep, re.IGNORECASE)
            job_title = pattern.sub(" ", job_title)
        '''

        safe_job = clean_name(job_title)
        safe_company = clean_name(company)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            
        # 3. 组合文件名
        filename = f"transcript_{mode}_{safe_job}_{safe_company}_{timestamp}.txt"
        return os.path.join(transcript_dir, filename)


    # [Helper] Log to Transcript
    def log_to_transcript(speaker, text):
        """Appends dialogue to the active transcript file if one exists."""
        path = app_state.get("transcript_path")
        if path:
            try:
                with open(path, "a", encoding="utf-8") as f:
                    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                    f.write(f"[{timestamp}] {speaker}: {text}\n\n")
            except Exception as e:
                logger.error(f"Failed to write transcript: {e}")



    logger.info(f"System Initializing... Model: {model_name}")
    logger.info(f"Memory Mode: {'LONG (Persistent)' if enable_long_memory else 'SHORT (Ephemeral)'}")

    # 3. Memory Initialization (Long-term Context)
    history_manager = None
    chat_history_text = ""
    if enable_long_memory:
        history_manager = HistoryManager(session_storage_dir, "my_career_chat", context_window)
        chat_history_text = history_manager.get_context_string()
        logger.info(f"Loaded history context ({len(chat_history_text)} chars).")
    else:
        chat_history_text = "No previous conversation history available."

    # 4. User Context Loading & Persona Injection
    logger.info("Loading user context...")
    repo_path = os.path.join(repo_dir, repo_file)
    user_manager = UserManager(profile_path=profile_file, repo_path=repo_path)
    full_context = user_manager.get_system_context()
    full_repo_text = user_manager.full_skill_text 
    
    profile_name = user_manager.profile_data.get('basic_info', {}).get('name', '')
    user_name = settings_user_name if settings_user_name else (profile_name if profile_name else 'User')

    # 5. Instructions
    instruct_config = load_instructions(instruct_file)
    if not instruct_config: return
    
    def inject_personas(text):
        if not text: return ""
        return text.replace("{copilot_name}", copilot_name)\
                   .replace("{interviewer_name}", interviewer_name)\
                   .replace("{candidate_name}", candidate_name)\
                   .replace("{avatar_name}", avatar_name)\
                   .replace("{user_name}", user_name) 

    cli_prompt_template = instruct_config["main"]["user_prompt_template"]

    # Prepare General Agent Instructions
    raw_advisor_template = instruct_config["general"]["advisor_instruction"]
    advisor_template_with_personas = inject_personas(raw_advisor_template)
    # Inject Chat History into the system prompt for continuity
    final_advisor_instruction = advisor_template_with_personas.replace("{chat_history}", chat_history_text) if "{chat_history}" in advisor_template_with_personas else advisor_template_with_personas

    # Prepare Prompts for Sub-Agents
    cv_agent_prompts = {
        "summarize": instruct_config["agents"]["summarize_instruction"],
        "finding": instruct_config["agents"]["finding_instruction"],
        "writer": instruct_config["agents"]["writer_instruction"],
        "validator": instruct_config["agents"]["validator_instruction"],
        "refiner": instruct_config["agents"]["refiner_instruction"]
    }
    
    mock_prompts = {
        "interviewer": inject_personas(instruct_config["agents"]["interviewer_instruction"]),
        "coach": inject_personas(instruct_config["agents"]["coach_instruction"]),
        "question_list": inject_personas(instruct_config["agents"]["question_list_instruction"])
    }

    copilot_prompts = {
        "copilot": inject_personas(instruct_config["agents"]["copilot_instruction"])
    }

    # 新增，读取 Instruction 里的 candidate_instruction
    candidate_prompts = {
        "candidate": inject_personas(instruct_config["agents"]["candidate_instruction"])
    }

    # [新增] Avatar Prompt
    # 这里不需要 inject_personas，因为 avatar_service 会自己处理 {repo_content} 和 {question}
    avatar_prompts = {
        "avatar": inject_personas(instruct_config["agents"]["avatar_instruction"])
    }

    # 6. Service Layer Initialization (The Spokes)
    # Service A: Generates Documents using RAG Loop
    cv_maker_service = CVMakerService(
        model_name=model_name,
        full_repo_text=full_repo_text, 
        prompts=cv_agent_prompts,
        export_dir=cv_dir, 
        max_iterations=max_iterations,
        user_prompt_template=cli_prompt_template
    )
    
    # Service B: Searches and Downloads Jobs via RapidAPI
    job_scout_service = JobScoutService(
        jd_dir=jd_dir,
        url_dir=url_dir,
        default_engine=default_engine,
        max_results=max_results
    )

    # Service C: Conducts Mock Interviews
    mock_interview_service = MockInterviewService(
        jd_dir=jd_dir,
        cv_dir=cv_dir,
        repo_path=os.path.join(repo_dir, repo_file),
        prompts=mock_prompts
    )

    # Service D: Provides Real-time Answers
    interview_copilot_service = InterviewCopilotService(
        jd_dir=jd_dir,
        cv_dir=cv_dir,
        repo_path=os.path.join(repo_dir, repo_file),
        prompts=copilot_prompts
    )

    # Service E: 初始化 Candidate Service (Tom)
    candidate_service = CandidateService(
        jd_dir=jd_dir,   # 新增
        cv_dir=cv_dir,   # 新增
        repo_path=os.path.join(repo_dir, repo_file),
        # 不需要调用voice_service。Tom只负责思考，说话由app.py 的主循环（或 start_auto_interview_tool）调用 voice_service.speak() 来把 Tom 生成的文本读出来的。
        prompts=candidate_prompts
    )

    # Service F: Multimodal Interaction (Voice)
    # =========================================================
    # [关键修复 / CRITICAL FIX]
    # 1. 先将 voice_service 定义为 None (防止 try 块失败后变量未定义)
    # 2. 使用 try-except 块包裹初始化，防止因音频设备问题崩溃
    # =========================================================
    voice_service = None
    try:
        voice_service = VoiceService()
        if voice_service.enabled:
            logger.info(f"Voice Mode Configured (Scope: {voice_service.scope})")
    except Exception as e:
        logger.error(f"VoiceService init failed: {e}", exc_info=True)
        print(f"[ERROR] VoiceService init failed: {e}") # 强制打印到控制台


    # Logic to determine if TTS/STT should be active based on current state
    def should_use_voice():
        if voice_service is None: return False # Safety check
        if not voice_service.enabled: return False
        if "all" in voice_service.scope: return True
        # Only enable voice if we are in an active interview session
        if app_state["is_interview_active"]:
            allowed_scopes = ["mock_interview", "mock_interview_service", "interview_copilot", "interview_copilot_service"]
            for s in voice_service.scope:
                if s in allowed_scopes:
                    return True
        return False

    # Service G: Avatar (Richard) 
    avatar_service = AvatarService(
        jd_dir=jd_dir,
        cv_dir=cv_dir,
        repo_path=os.path.join(repo_dir, repo_file),
        voice_service=voice_service,
        prompts=avatar_prompts
    )

    # Standalone LLM for Auto-Interview
    # Standalone LLM for Auto-Interview
    # 之前是 genai.GenerativeModel(...)
    # 现在直接使用 client 即可
    try:
        # 创建统一的 Client 客户端
        client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"), http_options={'api_version': 'v1alpha'})
        # 注意：新版 SDK 中不需要预先实例化 GenerativeModel 对象，直接通过 client 调用即可
        logger.info(f"New Google GenAI Client initialized for Model: {model_name}")
    except Exception as e:
        logger.error(f"Failed to init GenAI Client: {e}")
        client = None


    # ==========================================
    # 7. Tool Definitions (Agent Interface)
    # ==========================================

    # [Tool 1] Generate CVs (Batch or Single)
    async def process_files_tool(target_file: str = "ALL"):
        """Generate CVs/Resumes from files in the 'jd' folder."""
        clean_target = target_file.strip()

        # 1. Smart Find logic
        if "all" in clean_target.lower() and len(clean_target) < 10:
            final_target = "ALL"
        else:
            # 使用智能查找尝试找到最匹配的文件名
            final_target = smart_find_file(clean_target, jd_dir)

        # [新增关键逻辑] 检查是否找到了匹配
        # 如果 smart_find_file 返回的还是原始关键词，说明没有找到匹配的文件
        if final_target == clean_target and clean_target.upper() != "ALL":
            logger.warning(f"Smart find failed for '{clean_target}'. Sending error to LLM.")
            # 返回一个明确的错误信息给 LLM，触发它去询问用户
            return f"Error: Sorry, I couldn't find a file matching the keywords '{clean_target}' in the jd folder. Please ask me to 'list files' to see what's available."
        
        print(f"\n[System] 🛠️  Processing Target: '{clean_target}' -> Matched: '{final_target}'")

        # 如果匹配失败(返回了原词)，或者匹配成功，都传给 Service 的 run_batch_processing
        # 注意：Service 需要支持通过 substring 过滤
        result = await cv_maker_service.run_batch_processing(jd_dir, final_target)
        return result

    # [Tool 2] Generate CV from Text
    async def process_pasted_text_tool(jd_text: str):
        """Generate CV from pasted text content."""
        print(f"\n[System] 🛠️  Processing pasted text...")
        result = await cv_maker_service.process_jd_content(jd_text, source_name="Pasted_Text")
        return result

    # [Tool 3] Scout Jobs (Search + Download)
    async def find_and_download_jobs_tool(keywords: str, location: str = "au", period: str = "month", engine: str = "linkedin"):
        """Searches online jobs and downloads them locally."""
        print(f"\n[System] 🔍 Scout Running: {engine.upper()} | {keywords} | {location} | {period}...")
        result = job_scout_service.fetch_jobs_unified(keywords, location, period, engine, export_type="BOTH")
        return result

    # [Tool 4] Start Mock Interview
    async def start_mock_interview_tool(target_job: str):
        """Starts a mock interview session, loading relevant context files."""
        print(f"\n[System] 🎤 Preparing Interview Context for '{target_job}'...")
        materials = mock_interview_service.get_interview_materials(target_job)
        if not materials["ready"]:
            return f"Error: Could not find materials for '{target_job}'. Ensure you have a JD and Resume/CV in the folders."
        
        system_prompt = mock_interview_service.generate_system_prompt(materials)
        
        # [State Change] Lock the app into Interview Mode
        app_state["current_job_keyword"] = target_job
        app_state["is_interview_active"] = True

        # 3. [修改] 使用公共函数创建记录文件 (传入 Company)
        app_state["transcript_path"] = setup_transcript_file(
            mode="LIVE", 
            job_title=target_job, 
            company=materials.get('company', 'Unknown')
        )


        # [New] 面试开始，换一个新的面试官声音
        if voice_service.enabled:
            voice_service.pick_new_interviewer_voice()
        
        info_msg = f"""
✅ **Interview Ready!**
- Role: {materials['company']}
- JD: {materials['jd_file']}
- CV: {materials['cv_file']}
- Resume: {materials['resume_file']}
- Analysis: {materials.get('debug_file', 'N/A')}

(Switching to Hiring Manager: {interviewer_name}...)
"""
        print(info_msg)
        return system_prompt


    # [Tool 4b] 生成面试题库 (新增)
    async def generate_questions_tool(target_job: str):
        """
        Generates a comprehensive list of interview questions (Question Bank) for a specific job.
        Use this when the user wants to study alone, not roleplay.
        
        Args:
            target_job: Keywords to identify the job (e.g., "Reo Group").
        """
        print(f"\n[System] 📝 Generating Question Bank for '{target_job}'...")
        
        # 1. 复用查找逻辑
        materials = mock_interview_service.get_interview_materials(target_job)
        if not materials["ready"]:
            return f"Error: Could not find materials for '{target_job}'."

        # 2. [新增] 设置保存路径
        # 使用公共函数，Mode 设为 "QUESTION_BANK"
        bank_path = setup_transcript_file(
            mode="PREP", 
            job_title=target_job, 
            company=materials.get('company', 'Unknown')
        )
        
        # 将路径存入全局状态，主循环会自动把 Agent 生成的题目写入这个文件
        if bank_path: # [新增检查]，如文件未被创建，则不写
            app_state["transcript_path"] = bank_path
            print(f"[System] 💾 List will be saved to: {bank_path}")
        else:
            print(f"[System] 📝 Generating Question Bank (No file save)...")

        # 3. 生成 Prompt 并返回给 Agent
        # Agent 收到这个 Prompt 后，会立即执行指令，生成并打印题目列表
        prompt = mock_interview_service.generate_interview_questions_list(materials)
        return prompt


    # [Tool 5] Stop Interview & Review
    async def stop_interview_tool():
        """Ends the interview and provides feedback from the Coach."""
        if not app_state["is_interview_active"]:
            return "System Alert: No active interview to stop."

        print(f"\n[System] 🛑 Ending Interview. Switching to Coach Persona...")
        
        # [State Change] Unlock Interview Mode
        app_state["is_interview_active"] = False
        review_prompt = mock_interview_service.generate_review_prompt()
        return review_prompt

    # [Tool 6] Interview Copilot (Real-time Assist)
    async def ask_copilot_tool(question: str):
        """Provides real-time answers to interview questions using Skill Repo."""
        target_job = app_state.get("current_job_keyword")
        if not target_job:
            return "Error: No active job context. Please run 'Start Mock Interview' (or specify the job) first."

        print(f"\n[System] 🧠 {copilot_name} Thinking (Context: {target_job})...")
        materials = interview_copilot_service.get_context_materials(target_job)
        answer_prompt = interview_copilot_service.generate_answer_prompt(question, materials)
        return answer_prompt


    # [Tool 7] 自动对战模式 (Mary vs Tom)
    async def start_auto_interview_tool(target_job: str):
        """
        Starts a fully autonomous interview simulation between Mary (Interviewer) and Tom (Candidate).
        """
        print(f"\n[System] 🤖 Initializing Auto-Interview: {interviewer_name} vs. {candidate_name} for '{target_job}'...")
        

        # --- 1. 准备 Mary (面试官) 的材料 ---
        # Mary 只能看到有限的材料 (JD, Resume, CV)
        mary_materials = mock_interview_service.get_interview_materials(target_job)
        if not mary_materials["ready"]: 
            return f"Error: Materials not found for '{target_job}'."
        
        # 生成 Mary 的人设指令
        mary_system_prompt = mock_interview_service.generate_system_prompt(mary_materials)
        
        # --- 2. 准备 Tom (候选人) 的材料 ---
        # [修改] 直接获取 Tom 的完整上下文，不需要手动提取变量了
        tom_materials = candidate_service.get_context_materials(target_job)
        
        # --- 3. 初始化对话 ---
        mary_chat = client.aio.chats.create(
            model=model_name,
            history=[
                types.Content(role="user", parts=[types.Part.from_text(mary_system_prompt + "\n\n(System: Please start the interview now.)")]),
                types.Content(role="model", parts=[types.Part.from_text("Understood. I am ready.")])
            ]
        )

        max_turns = auto_interview_max_turns
        current_turn = 0


        # --- 4. 创建transacript文件保存对话内容 ---
        transcript_path = setup_transcript_file(
            mode="AUTO", 
            job_title=target_job, 
            company=mary_materials.get('company', 'Unknown')
        )
        
        # 定义本地的 log 函数，因为 Auto Tool 有自己的循环
        def log_auto(speaker, text):
            if transcript_path:
                try:
                    with open(transcript_path, "a", encoding="utf-8") as f:
                        f.write(f"{speaker}: {text}\n\n")
                except: pass 

        print(f"\n🔴 LIVE SESSION STARTED: {interviewer_name} <--> {candidate_name}\n")
        print(f"[System] 📝 Transcript saved to: {transcript_path}")
        

        # --- Mary 开场 ---
        try:
            response = await mary_chat.send_message("Start now.")
            mary_msg = response.text
        except Exception as e: return f"Error starting Mary: {e}"

        print(f"\n{interviewer_name} (Voice) > {mary_msg}")
        log_auto(interviewer_name, mary_msg) # [新增] 记录
        await voice_service.speak(mary_msg, persona="mary")
        
        # --- Loop ---
        while True:
            # 检查结束语
            if "goodbye" in mary_msg.lower() or "thank you for your time" in mary_msg.lower():
                print("\n[System] Mary signaled end of interview.")
                
                # [新增] 让 Tom 礼貌道别 (Polite Closing)
                tom_farewell = "Thank you very much for your time, Mary. I look forward to hearing from you. Goodbye."
                print(f"\n{candidate_name} (Voice) > {tom_farewell}")
                log_auto(candidate_name, tom_farewell)
                if voice_service: await voice_service.speak(tom_farewell, persona="tom")
                break

                
            # --- Tom 回答 ---
            print(f"\n[System] 🧠 {candidate_name} is thinking...")
            
            # [关键] 直接传入 mary_msg 和 tom_materials
            tom_prompt = candidate_service.generate_answer_prompt(mary_msg, tom_materials)
            
            try:
                tom_resp = await client.aio.models.generate_content(model=model_name, contents=tom_prompt)
                tom_msg = tom_resp.text
            except Exception as e: 
                print(f"Tom error: {e}"); break
                
            print(f"\n{candidate_name} (Voice) > {tom_msg}")
            log_auto(candidate_name, tom_msg) # [新增] 记录
            await voice_service.speak(tom_msg, persona="tom")
            
            current_turn += 1

            # 到达系统规定轮次，结束conversation
            if current_turn >= max_turns:
                # print("\n[System] Max turns reached. Signaling Mary to wrap up...")
                # [关键技巧] 欺骗 Mary，告诉她时间到了，让她说结束语
                # next_input_for_mary = "\n\n(System: The interview time is up. Please thank the candidate and end the interview now.)"
                # next_input_for_mary = "\n\n(System: The interview time is up. Please thank the candidate and end the interview now.)" + tom_msg
                print("\n[System] Max turns reached. Stop the conversation ...")
                break
            else:
                # 正常继续，把 Tom 的话传给 Mary
                next_input_for_mary = tom_msg


            # --- Mary 反应 ---
            try:
                response = await mary_chat.send_message(tom_msg)
                mary_msg = response.text
            except Exception as e: 
                print(f"Mary error: {e}"); break
                
            print(f"\n{interviewer_name} (Voice) > {mary_msg}")
            log_auto(interviewer_name, mary_msg) # [新增] 记录
            await voice_service.speak(mary_msg, persona="mary")
            


        print(f"\n[System] 🏁 Auto-Interview Finished ({current_turn} turns).")
        return "Simulation complete."


    # [Tool 9] Richard 替身模式 (Real World Interview)
    async def start_avatar_mode_tool(target_job: str):
        """
        Activates 'Richard', the AI Avatar, to take a real-world interview on my behalf.
        He listens to the microphone and answers questions automatically.
        
        Args:
            target_job: Keywords to identify the job (e.g. "Reo Group").
        """

        # 1. [新增] 创建记录文件
        materials = mock_interview_service.get_interview_materials(target_job) # 获取公司名用于文件名
        company_name = materials.get('company', 'Unknown')
        
        transcript_path = setup_transcript_file(
            mode="AVATAR", 
            job_title=target_job, 
            company=company_name
        )
        print(f"[System] 📝 Transcript saved to: {transcript_path}\n")
        
        # 2. [新增] 定义日志回调函数
        def log_callback(speaker, text):
            if transcript_path: # [新增检查], 如果文件没有被创建，则退出不写
                try:
                    with open(transcript_path, "a", encoding="utf-8") as f:
                        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                        f.write(f"[{timestamp}] {speaker}: {text}\n\n")
                except: pass


        # 定义一个回调函数，供 AvatarService 调用 LLM
        # 这样 Service 层就不需要依赖具体的 genai 库
        # 3. LLM 生成器回调 (保持不变)
        async def llm_generator(prompt):
            try:
                if client:
                    resp = await client.aio.models.generate_content(model=model_name, contents=prompt)
                    return resp.text
                else:
                    return "Error: LLM Model not initialized."
            except Exception as e:
                logger.error(f"LLM Generation failed: {e}")
                return "I'm having trouble thinking right now."


        # 4. [修改] 启动循环，传入 log_callback
        result = await avatar_service.run_avatar_session(
            target_job=target_job, 
            llm_generator_func=llm_generator,
            log_callback=log_callback # 传入记录功能
        )
        return result


    # [Tool 7] Profile Manager
    async def update_profile_tool(category: str, key: str, value: str):
        """Updates user preferences in the JSON profile."""
        print(f"\n[System] 📝 Updating Profile: [{category}] {key} = {value}...")
        profile_path = user_manager.profile_path
        try:
            if os.path.exists(profile_path):
                with open(profile_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            else:
                data = {}
            if category not in data: data[category] = {}
            data[category][key] = value
            with open(profile_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            user_manager.profile_data = data
            return f"✅ Successfully updated profile: {category}.{key} is now '{value}'."
        except Exception as e:
            logger.error(f"Profile update failed: {e}")
            return f"Error updating profile: {str(e)}"


    # Register all tools
    tools_list = [
        FunctionTool(process_files_tool),
        FunctionTool(process_pasted_text_tool),
        FunctionTool(find_and_download_jobs_tool),
        FunctionTool(start_mock_interview_tool),
        FunctionTool(start_auto_interview_tool), # [新增]
        FunctionTool(generate_questions_tool),
        FunctionTool(stop_interview_tool),
        FunctionTool(ask_copilot_tool),
        FunctionTool(start_avatar_mode_tool), # [新增]
        FunctionTool(update_profile_tool)
    ]

    # 8. Create Agent & Runner
    advisor_agent = create_general_advisor(
        model_name=model_name, 
        user_context=full_context, 
        instruction_template=final_advisor_instruction, # Configured Instructions
        tools=tools_list 
    )

    adk_session_id = str(uuid.uuid4()) 
    user_id = "local_user"
    app_name = "career_copilot"

    session_service = InMemorySessionService()
    runner = Runner(agent=advisor_agent, app_name=app_name, session_service=session_service)
    await session_service.create_session(session_id=adk_session_id, user_id=user_id, app_name=app_name)

    # ========================
    # start to deleted
    '''
        # 9. Interaction Loop (Main Event Cycle)
        memory_status_str = f" | 🧠 Memory: {len(history_manager.history)} turns" if (enable_long_memory and history_manager and history_manager.history) else ""
        
        ...
        ...
        
        except KeyboardInterrupt: 
            break
        except Exception as e: 
            logger.error(f"Main Loop Error: {e}")
    '''
    # end to deleted
    # =========================

    # [Return backend context]
    # This dictionary contains everything the UI (CLI or Streamlit) needs to function.
    return {
        "runner": runner,
        "voice_service": voice_service,
        "adk_session_id": adk_session_id,
        "user_id": user_id,
        "app_state": app_state,
        "history_manager": history_manager,
        "enable_long_memory": enable_long_memory,
        "user_name": user_name,
        "copilot_name": copilot_name,
        "interviewer_name": interviewer_name,
        "candidate_name": candidate_name,
        "avatar_name" : avatar_name,
        "instruct_config": instruct_config,
        "should_use_voice": should_use_voice,
        "log_to_transcript": log_to_transcript
    }



if __name__ == "__main__":
    # For testing, run the CLI directly
    # In the new architecture, you would run app_cli.py instead
    print("Please run 'app_cli.py' to start the application.")