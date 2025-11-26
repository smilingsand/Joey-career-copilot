"""
Project: Joey - The Voice-Enabled End-to-End Career Copilot
File: app.py
Description: 
    This is the Central Hub (Entry Point) of the application. 
    It acts as the 'Router Agent' responsible for:
    1. Initializing the Google Gemini Model and ADK Components.
    2. Orchestrating four specialized services (Job Scout, CV Maker, Mock Interview, Copilot).
    3. Managing global application state (e.g., Interview Mode vs. General Chat).
    4. Handling multimodal input/output (Voice/Text).
    5. Routing user intents to the appropriate Function Tools.
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
from dotenv import load_dotenv, find_dotenv

# --- Google Agents Development Kit (ADK) Imports ---
from google.adk.runners import Runner 
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool 
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

# ==========================================
# Observability & Logging Configuration
# ==========================================
# Set global level to WARNING to reduce noise from third-party libraries
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(message)s')
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("google_genai").setLevel(logging.WARNING)
logging.getLogger("google_adk").setLevel(logging.WARNING)

# Enable INFO logging for core business logic to trace Agent reasoning
logger = logging.getLogger("App")
logger.setLevel(logging.INFO)
logging.getLogger("JobScoutService").setLevel(logging.INFO)
logging.getLogger("MockInterviewService").setLevel(logging.INFO)
logging.getLogger("InterviewCopilotService").setLevel(logging.INFO)
logging.getLogger("CVMakerService").setLevel(logging.INFO)

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
# Main Application Loop
# ==========================================
async def main():
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
    
    # Workflow Settings
    max_iterations = int(settings['Workflow']['max_loop_iterations'])
    session_storage_dir = settings['Paths']['session_dir']
    context_window = int(settings['Memory']['context_window_turns'])
    enable_long_memory = settings.getboolean('Memory', 'enable_long_memory', fallback=True)

    default_engine = settings['Search']['default_engine']
    max_results = settings['Search']['max_results']
    
    # Persona Configuration (Decoupling names from code)
    copilot_name = settings.get('Personas', 'copilot_name', fallback='Joey')
    interviewer_name = settings.get('Personas', 'interviewer_name', fallback='Mary')
    settings_user_name = settings.get('Personas', 'user_name', fallback='')

    # [State Machine] Global App State
    # Used to handle complex interaction logic (e.g., preventing exit during interviews)
    app_state = {
        "is_interview_active": False,
        "current_job_keyword": None
    }

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

    # 4. Context Loading & Persona Injection
    logger.info("Loading user context...")
    repo_path = os.path.join(repo_dir, repo_file)
    user_manager = UserManager(profile_path=profile_file, repo_path=repo_path)
    full_context = user_manager.get_system_context()
    full_repo_text = user_manager.full_skill_text 
    
    # Determine User Name
    profile_name = user_manager.profile_data.get('basic_info', {}).get('name', '')
    user_name = settings_user_name if settings_user_name else (profile_name if profile_name else 'User')

    # Load Prompt Templates
    instruct_config = load_instructions(instruct_file)
    if not instruct_config: return
    
    # Helper: Inject persona names into raw prompt templates
    def inject_personas(text):
        if not text: return ""
        return text.replace("{copilot_name}", copilot_name)\
                   .replace("{interviewer_name}", interviewer_name)\
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
        "coach": inject_personas(instruct_config["agents"]["coach_instruction"])
    }

    copilot_prompts = {
        "copilot": inject_personas(instruct_config["agents"]["copilot_instruction"])
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

    # Service E: Multimodal Interaction (Voice)
    voice_service = VoiceService()
    if voice_service.enabled:
        logger.info(f"Voice Mode Configured (Scope: {voice_service.scope})")

    # Logic to determine if TTS/STT should be active based on current state
    def should_use_voice():
        if not voice_service.enabled: return False
        if "all" in voice_service.scope: return True
        # Only enable voice if we are in an active interview session
        if app_state["is_interview_active"]:
            allowed_scopes = ["mock_interview", "mock_interview_service", "interview_copilot", "interview_copilot_service"]
            for s in voice_service.scope:
                if s in allowed_scopes:
                    return True
        return False

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
        FunctionTool(stop_interview_tool),
        FunctionTool(ask_copilot_tool),
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

    # 9. Interaction Loop (Main Event Cycle)
    memory_status_str = f" | 🧠 Memory: {len(history_manager.history)} turns" if (enable_long_memory and history_manager and history_manager.history) else ""
    
    # Print Welcome Interface
    try:
        welcome_raw = instruct_config["interface"]["welcome_message"]
        welcome_msg = inject_personas(welcome_raw).replace("{memory_status}", memory_status_str)
        print(welcome_msg)
    except KeyError:
        print(f"\n🤖 Hi {user_name}, {copilot_name} Ready!\n")
    
    while True:
        try:
            user_input = ""
            # Check if we should activate the microphone
            use_voice_input = should_use_voice()
            
            if use_voice_input:
                voice_text = voice_service.listen()
                if voice_text:
                    user_input = voice_text
                    print(f"\n{user_name} (Voice) > {user_input}")
            
            # Fallback to keyboard if no voice detected or voice disabled
            if not user_input:
                prompt_symbol = "🎤 >" if use_voice_input else ">"
                user_input = input(f"\n{user_name} {prompt_symbol} ")

            # [Smart Exit Logic]
            # Handles "Dual-Layer" exit: Stop Interview vs Exit App
            sentences = re.split(r'[.!?;]+', user_input.lower())
            exit_keywords = {"exit", "quit", "stop", "bye", "goodbye", "terminate", "shutdown", "end"}
            is_exit_command = False
            
            for sentence in sentences:
                words = sentence.strip().split()
                if not words: continue
                
                has_exit_word = any(w in exit_keywords for w in words)
                is_short = len(words) <= 5
                has_negation = any(w in ["not", "don't", "dont", "never"] for w in words)
                
                if has_exit_word and is_short and not has_negation:
                    is_exit_command = True
                    break

            if is_exit_command:
                if app_state["is_interview_active"]:
                    print("\n[System] Detected exit command. Ending interview session...")
                    # Redirect intent to stop_interview_tool
                    user_input = "Stop interview and give feedback."
                else:
                    print("Bye!")
                    break
            
            if not user_input.strip(): continue

            # Send to Agent
            msg = types.UserContent(parts=[types.Part(text=user_input)])
            
            if not use_voice_input:
                print("Agent > ", end="", flush=True)
            
            agent_response_buffer = ""
            
            # Run Agent Logic
            async for event in runner.run_async(
                new_message=msg, 
                session_id=adk_session_id, 
                user_id=user_id
            ):
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.text:
                            print(part.text, end="", flush=True)
                            agent_response_buffer += part.text
            print("") 
            
            # TTS Output
            if should_use_voice() and agent_response_buffer:
                await voice_service.speak(agent_response_buffer)

            # Save to Memory
            if enable_long_memory and history_manager and agent_response_buffer:
                history_manager.add_turn(user_input, agent_response_buffer)

        except KeyboardInterrupt: 
            break
        except Exception as e: 
            logger.error(f"Main Loop Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
    