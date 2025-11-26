import os
import logging
import asyncio
import re
import json
import uuid
import tomllib
import configparser  # [新增] 用于读取 ini
import shutil
from datetime import datetime
from dotenv import load_dotenv, find_dotenv

# Google ADK
from google.genai import types  # 确保引入这个，用于构造 UserContent
from google.adk.runners import InMemoryRunner
from google.adk.runners import Runner
#from google.adk.sessions import DatabaseSessionService
from google.adk.sessions import InMemorySessionService

# 本地模块
from utils.file_handler import FileLoader
from tools.skill_store import SkillStore
from agents.cv_pipeline import create_cv_pipeline

# 导入公共类
from context.job_context import JobContext


# ==========================================
# Logging Configuration
# ==========================================
# 1. 基础配置：设置全局为 INFO，这样你能看到自己打印的业务流程
logging.basicConfig(
    level=logging.INFO,     # logging.WARNING, logging.ERROR
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# 2. [关键修改] 屏蔽第三方库的啰嗦日志
# 将这些库的级别设为 WARNING，只有出错了才会打印，平时保持安静
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("google_genai").setLevel(logging.WARNING)
logging.getLogger("google_adk").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# 3. 获取当前模块的 Logger
logger = logging.getLogger("Main")


# ==========================================
# Configuration Loader
# ==========================================
def load_settings(ini_path="settings.ini"):
    if not os.path.exists(ini_path):
        logger.error(f"Settings file not found: {ini_path}")
        return None
    
    config = configparser.ConfigParser()
    config.read(ini_path, encoding='utf-8')
    return config

# ==========================================
# Helper Functions
# ==========================================
def setup_directories(config):
    """根据配置创建目录"""
    dirs = [
        config['Paths']['input_dir'], 
        config['Paths']['export_dir'], 
        config['Paths']['repo_dir']
    ]
    for d in dirs:
        os.makedirs(d, exist_ok=True)
    
    # [修改] 归档逻辑
    export_dir = config['Paths']['export_dir']
    if not os.path.exists(export_dir):
        return

    # 筛选出需要归档的文件 (排除 Archive 文件夹本身)
    files_to_move = [f for f in os.listdir(export_dir) 
                     if os.path.isfile(os.path.join(export_dir, f)) and not f.startswith('.')]
    
    if not files_to_move:
        return

    # 创建归档目录: Export/Archive/YYYYMMDD_HHMMSS
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_root = os.path.join(export_dir, "Archive")
    current_archive_folder = os.path.join(archive_root, timestamp)
    
    logger.info(f"Archiving {len(files_to_move)} old files to: {current_archive_folder}")
    os.makedirs(current_archive_folder, exist_ok=True)

    for f in files_to_move:
        src_path = os.path.join(export_dir, f)
        dst_path = os.path.join(current_archive_folder, f)
        try:
            shutil.move(src_path, dst_path)
        except Exception as e:
            logger.warning(f"Failed to archive {f}: {e}")


def sanitize_filename(name: str) -> str:
    if not name: return "Unknown"
    name_str = str(name)
    cleaned = re.sub(r'[\\/*?:"<>|,\.\n\r\t]', " ", name_str)
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return cleaned.strip()[:60]

def generate_base_name(metadata: dict, seq_num: int) -> str:
    date_str = datetime.now().strftime("%Y%m%d")
    seq_str = f"{seq_num:03d}"
    comp = metadata.get("company", "Unknown Company")
    title = metadata.get("title", "Unknown Title")
    safe_company = sanitize_filename(comp)
    safe_title = sanitize_filename(title)
    # Format: Title_Company_Date_Seq
    return f"{safe_title}_{safe_company}_{date_str}_{seq_str}"

def save_documents(ctx, file_loader: FileLoader, base_name: str, export_dir: str):
    if not ctx.is_valid_for_saving():
        logger.error(f"STOP: Document generation failed for file {ctx.file_name}.")
        return

    logger.info(f"Saving documents with base name: {base_name}")
    cl_filename = f"CoverLetter_{base_name}.docx"
    sum_filename = f"PersonalSummary_{base_name}.docx"

    if ctx.cover_letter_content:
        file_loader.save_docx(ctx.cover_letter_content, os.path.join(export_dir, cl_filename))
    if ctx.resume_summary_content:
        file_loader.save_docx(ctx.resume_summary_content, os.path.join(export_dir, sum_filename))

def load_instructions(file_path):
    if not os.path.exists(file_path):
        logger.error(f"Instruction file not found: {file_path}")
        return None
    try:
        with open(file_path, "rb") as f:
            return tomllib.load(f)
    except Exception as e:
        logger.error(f"Failed to parse instructions: {e}")
        return None


# ==========================================
# Main Logic
# ==========================================
async def main():
    if "GOOGLE_API_KEY" not in os.environ:
        logger.error("CRITICAL: GOOGLE_API_KEY not found.")
        return

    # 1. 加载 Settings
    settings = load_settings()
    if not settings: return
    
    # 提取路径配置
    INPUT_DIR = settings['Paths']['input_dir']
    EXPORT_DIR = settings['Paths']['export_dir']
    REPO_DIR = settings['Paths']['repo_dir']
    REPO_FILE = settings['Paths']['repo_filename']
    INSTRUCT_FILE = settings['Paths']['instruction_file']
    
    MODEL_NAME = settings['Model']['model_name']
    MAX_ITERATIONS = int(settings['Workflow']['max_loop_iterations'])

    # 2. 加载 Instructions
    config = load_instructions(INSTRUCT_FILE)
    if not config: return
    
    user_prompt_template = config["main"]["user_prompt_template"]
    agent_prompts = {
        "summarize": config["agents"]["summarize_instruction"],
        "finding": config["agents"]["finding_instruction"],
        "writer": config["agents"]["writer_instruction"],
        "validator": config["agents"]["validator_instruction"],
        "refiner": config["agents"]["refiner_instruction"]
    }

    setup_directories(settings)
    file_loader = FileLoader()
    
    repo_full_path = os.path.join(REPO_DIR, REPO_FILE)
    if not os.path.exists(repo_full_path):
        logger.error(f"Repo missing: {repo_full_path}")
        return
    
    skill_store = SkillStore(repo_full_path)
    full_repo_text = skill_store.get_formatted_repo_content()
    logger.info(f"Loaded Repo Text: {len(full_repo_text)} chars")
    
    try:
        # 创建 Pipeline Agent
        cv_pipeline = create_cv_pipeline(
            model_name=MODEL_NAME, 
            full_repo_text=full_repo_text,     
            prompts=agent_prompts,
            max_iterations=MAX_ITERATIONS # 传入配置
        )
    except Exception as e:
        logger.error(f"Agent Init Failed: {e}")
        return

    files = [f for f in os.listdir(INPUT_DIR) if not f.startswith('.')]

    for i, filename in enumerate(files):
        file_path = os.path.join(INPUT_DIR, filename)
        logger.info(f"\n\n")
        logger.info(f"########## Start processing File [{i}]: {filename} ########## ")
        
        jd_text = file_loader.load(file_path)
        if not jd_text: continue

        ctx = JobContext(file_path)
        user_prompt = user_prompt_template.replace("{jd_text}", jd_text)
        
        try:
            '''
            runner = InMemoryRunner(agent=root_agent)
            response_events = await runner.run_debug(user_prompt, verbose=False)
            for event in response_events:
            # if use run_debug, then comment below step 1-5
            '''

            # 1. 生成 Session ID 和 user_id
            user_id = "local_user"
            session_id = str(uuid.uuid4())
            app_name = "resume_generator"

            # 2. 创建 Session Management
            # InMemorySessionService stores conversations in RAM (temporary)
            session_service = InMemorySessionService()

            # 2. 创建 Runner
            runner = Runner(agent=cv_pipeline, app_name=app_name, session_service=session_service)
            
            # 3. [关键修正] 显式创建会话！ 必须先告诉 service："我要用这个 ID，请帮我建档"
            session = await session_service.create_session(
                app_name=app_name, 
                user_id=user_id, 
                session_id=session_id
            )
            logger.info(f"Session initialized:{session_id}, APP:{app_name}, User:{user_id}")

            # 4. 构造标准消息对象 (run_async 需要更严谨的参数)
            user_message = types.UserContent(parts=[types.Part(text=user_prompt)])

            # 5. [关键修正] 使用 run_async 并传入必要参数
            logger.info("Pipeline started...")
            async for event in runner.run_async(
                new_message=user_message,
                session_id=session_id,  # [新增] 必须指定会话ID
                user_id=user_id         # [新增] 必须指定用户ID
            ):

                # 6. 应用流程
                agent_name = getattr(event, 'author', 'Unknown')
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.text: ctx.parse_event_text(part.text, agent_name)

            # 7. 保存结果
            base_name = generate_base_name(ctx.job_metadata, i)
            save_documents(ctx, file_loader, base_name, EXPORT_DIR)
            ctx.save_debug_json(EXPORT_DIR, base_name)
            
            logger.info(f"########## Completed processing for {filename}##########")

        except Exception as e:
            logger.error(f"########## Error processing {filename}: {e} ##########", exc_info=True)

if __name__ == "__main__":
    logger.info("System Initializing...")
    env_file = find_dotenv()
    if env_file: load_dotenv(env_file, override=True)
    else: load_dotenv(override=True)

    if os.getenv("GOOGLE_API_KEY"):
        asyncio.run(main())
    else:
        logger.error("Please configure GOOGLE_API_KEY in .env file.")