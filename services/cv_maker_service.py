"""
Project: Joey - The Voice-Enabled End-to-End Career Copilot
File: services/cv_maker_service.py
Description: 
    This service orchestrates the core "Creation" phase of the workflow.
    It encapsulates the Multi-Agent RAG pipeline (Summarizer -> Finder -> Writer -> Validator)
    to generate tailored CVs and Cover Letters based on Job Descriptions.
"""

import os
import logging
import uuid
import asyncio
import shutil
from datetime import datetime

# --- Google ADK & Types ---
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

# --- Local Modules ---
from utils.file_handler import FileLoader
from agents.cv_pipeline import create_cv_pipeline
from context.job_context import JobContext

logger = logging.getLogger("CVMakerService")

class CVMakerService:
    """
    Service responsible for generating tailored application materials.
    It wraps the complex Multi-Agent pipeline and handles I/O operations.
    """

    def __init__(self, model_name, full_repo_text, prompts, export_dir, max_iterations=3, user_prompt_template=None):
        """
        Initialize the CV Maker Service.
        
        Args:
            model_name: The Gemini model version to use (e.g., "gemini-2.0-flash-001").
            full_repo_text: The candidate's full skill repository (Knowledge Base).
            prompts: A dictionary of system instructions for each sub-agent.
            export_dir: Directory path where generated documents will be saved.
            max_iterations: Limit for the Validator-Refiner loop to prevent infinite cycles.
        """
        self.export_dir = export_dir
        self.file_loader = FileLoader()
        
        logger.info(f"Initializing CVMakerService with model: {model_name}")
        
        # Factory method to create the configured Multi-Agent Pipeline
        self.cv_pipeline_agent = create_cv_pipeline(
            model_name=model_name,
            full_repo_text=full_repo_text,
            prompts=prompts,
            max_iterations=max_iterations
        )
        
        # Template for injecting the JD text into the agent's context
        self.user_prompt_template = user_prompt_template or """
        Here is the JD content to process:

        {jd_text}

        Please start the analysis pipeline.
        """

    def _sanitize_filename(self, name: str) -> str:
        """Helper: Cleans strings to be safe for filesystem usage."""
        if not name: return "Unknown"
        import re
        name_str = str(name)
        # Replace illegal characters with space
        cleaned = re.sub(r'[\\/*?:"<>|,\.\n\r\t]', " ", name_str)
        cleaned = re.sub(r'\s+', ' ', cleaned)
        return cleaned.strip()[:60]

    def _generate_base_name(self, metadata: dict, seq_num: int = None) -> str:
        """Generates a standardized filename: Title_Company_Date_Seq."""
        date_str = datetime.now().strftime("%Y%m%d")
        
        if seq_num is not None:
            suffix = f"{seq_num:03d}"
        else:
            suffix = uuid.uuid4().hex[:4]
        
        comp = metadata.get("company", "Unknown Company")
        title = metadata.get("title", "Unknown Title")
        safe_company = self._sanitize_filename(comp)
        safe_title = self._sanitize_filename(title)
        
        return f"{safe_title}_{safe_company}_{date_str}_{suffix}"

    def _clear_export_directory(self):
        """
        Archives existing files in the Export directory to keep the workspace clean.
        Files are moved to 'Export/Archive/{Timestamp}/'.
        """
        if not os.path.exists(self.export_dir):
            return
            
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_root = os.path.join(self.export_dir, "Archive")
        current_archive_folder = os.path.join(archive_root, timestamp)
        
        # Find non-hidden files to move
        files_to_move = [f for f in os.listdir(self.export_dir) 
                         if os.path.isfile(os.path.join(self.export_dir, f)) and not f.startswith('.')]
        
        if not files_to_move:
            return

        logger.info(f"Archiving {len(files_to_move)} old files to: {current_archive_folder}")
        os.makedirs(current_archive_folder, exist_ok=True)

        for f in files_to_move:
            src_path = os.path.join(self.export_dir, f)
            dst_path = os.path.join(current_archive_folder, f)
            try:
                shutil.move(src_path, dst_path)
            except Exception as e:
                logger.warning(f"Failed to archive {f}: {e}")

    async def process_jd_content(self, jd_content: str, source_name="User_Input", seq_num: int = None):
        """
        [Core Function] Processes a single Job Description text to generate CV & Cover Letter.
        
        Flow:
        1. Setup unique ADK session.
        2. Execute the Multi-Agent Pipeline (Summarize -> Find -> Write -> Validate).
        3. Parse agent events to extract content.
        4. Save generated documents to disk.
        """
        logger.info(f"Starting CV generation task for source: {source_name}")
        
        if not jd_content or len(jd_content.strip()) < 50:
            return "Error: The provided Job Description content is too short or empty."

        # Initialize context to store intermediate and final results
        ctx = JobContext(source_name)
        user_prompt = self.user_prompt_template.replace("{jd_text}", jd_content)

        try:
            # 1. Session Setup (Isolated state for this run)
            user_id = "local_user"
            session_id = str(uuid.uuid4())
            app_name = "cv_maker_service"
            
            # Using InMemory storage for session state
            session_service = InMemorySessionService()
            runner = Runner(agent=self.cv_pipeline_agent, app_name=app_name, session_service=session_service)
            
            await session_service.create_session(
                session_id=session_id, 
                user_id=user_id, 
                app_name=app_name
            )

            # 2. Execute Agent Pipeline
            msg = types.UserContent(parts=[types.Part(text=user_prompt)])
            
            # Use run_async to handle streaming events from the agent
            async for event in runner.run_async(
                new_message=msg, 
                session_id=session_id, 
                user_id=user_id
            ):
                # 3. Event Parsing
                # Extract structured data or text chunks produced by different agents
                agent_name = getattr(event, 'author', 'Unknown')
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if part.text: 
                            # JobContext handles the logic of assigning text to correct fields
                            # (e.g., if Summarizer speaks, store in job_metadata)
                            ctx.parse_event_text(part.text, agent_name)

            # 4. Result Validation & Saving
            if not ctx.is_valid_for_saving():
                return f"❌ Failed: The AI could not extract valid information or generate content for '{source_name}'."

            base_name = self._generate_base_name(ctx.job_metadata, seq_num)
            
            cl_filename = f"CoverLetter_{base_name}.docx"
            sum_filename = f"PersonalSummary_{base_name}.docx"
            
            # Save Cover Letter
            if ctx.cover_letter_content:
                self.file_loader.save_docx(ctx.cover_letter_content, os.path.join(self.export_dir, cl_filename))
                
            # Save Resume Summary
            if ctx.resume_summary_content:
                self.file_loader.save_docx(ctx.resume_summary_content, os.path.join(self.export_dir, sum_filename))
            
            # Save Debug Data (Crucial for Mock Interview Service context)
            ctx.save_debug_json(self.export_dir, base_name)

            return (
                f"✅ Success! Generated documents for **{ctx.job_metadata.get('title')}**.\n"
                f"Files:\n- {cl_filename}\n- {sum_filename}"
            )

        except Exception as e:
            logger.error(f"Service Error processing {source_name}: {e}", exc_info=True)
            return f"❌ System Error: {str(e)}"

    async def run_batch_processing(self, input_dir: str, target_file: str = "ALL"):
        """
        Batch processes files from the Input (JD) directory.
        Supports filtering by filename.
        """
        if not os.path.exists(input_dir):
            return f"Error: Input directory '{input_dir}' not found."

        # Auto-archive old outputs if running a full batch
        self._clear_export_directory()

        files = [f for f in os.listdir(input_dir) if not f.startswith('.')]
        
        # Filter logic for single-file mode
        if target_file.upper() != "ALL":
            files = [f for f in files if target_file.lower() in f.lower()]
        
        if not files:
            return f"No files found matching '{target_file}' in {input_dir}."

        results = []
        print(f"\n[Service] Batch Processing Started. Found {len(files)} files.\n")
        
        # Process each file sequentially
        for i, f in enumerate(files):
            file_path = os.path.join(input_dir, f)
            content = self.file_loader.load(file_path)
            
            if not content:
                results.append(f"⚠️ Skipped empty/unreadable file: {f}")
                continue
            
            # Call core processing logic
            res = await self.process_jd_content(content, source_name=f, seq_num=i)
            results.append(f"[{f}]: {res}")
            print(f"[Service] Finished processing: {f}")

        return "\n\n".join(results)
        
