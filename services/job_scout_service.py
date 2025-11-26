"""
Project: Joey - The Voice-Enabled End-to-End Career Copilot
File: services/job_scout_service.py
Description: 
    This service acts as the 'Eyes' of the agent. It interacts with external APIs 
    (LinkedIn & Google Jobs via RapidAPI) to search for live job listings.
    
    Key Capabilities:
    1. Dual-Engine Search: Seamlessly switches between LinkedIn and Google Jobs.
    2. Data Normalization: Maps diverse API schemas to a unified internal format.
    3. Content Cleaning: Converts raw HTML/text into LLM-friendly Markdown.
    4. Workspace Management: Auto-archives old search results to keep context fresh.
"""

import os
import logging
import shutil
import re
import uuid
from datetime import datetime, timedelta
from tools.rapid_api_client import RapidAPIClient
import pycountry

logger = logging.getLogger("JobScoutService")

class JobScoutService:
    """
    A unified service for scouting job opportunities.
    It manages the end-to-end flow: Query -> Fetch -> Normalize -> Save.
    """
    def __init__(self, jd_dir, url_dir, default_engine="linkedin", max_results=10):
        """
        Initialize the Job Scout Service.

        Args:
            jd_dir: Directory to save detailed Job Description (Markdown) files.
            url_dir: Directory to save summary URL lists.
            default_engine: 'linkedin' or 'google'.
            max_results: Safety limit for the number of jobs to fetch per run.
        """
        self.jd_dir = jd_dir
        self.url_dir = url_dir
        self.default_engine = default_engine
        self.max_results = int(max_results)

        # Initialize the low-level API client
        self.client = RapidAPIClient()
        
        # Ensure persistence directories exist
        if not os.path.exists(self.jd_dir):
            os.makedirs(self.jd_dir)

    # ==========================================
    # Helper Methods (Utils & Cleaning)
    # ==========================================

    def _sanitize(self, name):
        """Sanitizes strings to be safe for filesystem naming conventions."""
        if not name: return "Unknown"
        # 替换非法文件名字符
        return re.sub(r'[\\/*?:"<>|,\.\n\r\t]', "_", str(name)).strip()[:40]

    def _archive_directory(self, dir_path):
        """
        [Workspace Management] 
        Moves existing files to a timestamped archive folder before a new search.
        This prevents the LLM from getting confused by stale data from previous sessions.
        """
        if not os.path.exists(dir_path): return

        # Filter for files only (exclude the Archive folder itself)
        files_to_move = [f for f in os.listdir(dir_path) 
                         if os.path.isfile(os.path.join(dir_path, f)) and not f.startswith('.')]
        if not files_to_move: return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_root = os.path.join(dir_path, "Archive")
        current_archive_folder = os.path.join(archive_root, timestamp)
        os.makedirs(current_archive_folder, exist_ok=True)
        
        for f in files_to_move:
            try:
                shutil.move(os.path.join(dir_path, f), os.path.join(current_archive_folder, f))
            except Exception as e:
                logger.warning(f"Failed to archive {f}: {e}")
        logger.info(f"Archived {len(files_to_move)} files in '{os.path.basename(dir_path)}'.")

    def _get_country_name(self, code):
        """Converts ISO codes (e.g., 'au') to full names (e.g., 'Australia') for LinkedIn API."""
        try:
            country = pycountry.countries.get(alpha_2=code.upper())
            return country.name if country else code
        except:
            return code

    def _get_timestamp_filter(self, period):
        """
        Converts relative time descriptions (e.g., 'week') into specific UTC timestamps.
        Required for LinkedIn API date filtering.
        """
        now = datetime.utcnow()
        if period == 'today':
            delta = timedelta(days=1)
        elif period == '3days':
            delta = timedelta(days=3)
        elif period == 'week':
            delta = timedelta(days=7)
        elif period == 'month':
            delta = timedelta(days=30)
        else:
            delta = timedelta(days=30) # Default
            
        past_time = now - delta
        return past_time.strftime('%Y-%m-%dT%H:%M:%S')

    def _format_location(self, loc_data):
        """Flattens location lists into a single readable string."""
        if isinstance(loc_data, list):
            return ", ".join(filter(None, loc_data))
        return str(loc_data) if loc_data else "Unknown Location"

    def _clean_description(self, text):
        """
        [Text Preprocessing]
        Cleans raw API text to ensure high-quality input for the LLM.
        - Fixes escaped characters.
        - Normalizes paragraph spacing (Double newline for Markdown).
        """
        if not text: return ""
        
        # 1. Fix literal escape characters often returned by JSON APIs
        text = text.replace("\\n", "\n").replace("\\t", "\t")
        
        # 2. Convert tabs to spaces for consistent rendering
        text = text.replace("\t", "    ")
        
        # 3. Ensure proper Markdown paragraph separation (Single \n -> Double \n)
        text = text.replace('\n', '\n\n')
        
        # 4. Collapse excessive vertical whitespace (3+ newlines -> 2)
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        return text.strip()


    def _extract_job_info(self, job_data, source):
        """
        [Data Normalization Layer]
        Crucial step: Maps divergent schemas from different APIs (LinkedIn vs Google)
        into a single, unified dictionary format for the rest of the system.
        """
        info = {}

        if source == "LINKEDIN":
            # --- Schema A: LinkedIn API ---
            info['title'] = job_data.get('title', 'Unknown Role')
            info['company'] = job_data.get('organization', 'Unknown Company')
            
            # LinkedIn returns location as a list
            loc_raw = job_data.get('locations_derived')
            if isinstance(loc_raw, list):
                info['location'] = ", ".join(filter(None, loc_raw))
            else:
                info['location'] = str(loc_raw) if loc_raw else "Unknown"
                
            info['url'] = job_data.get('url', '')
            # Prefer pre-extracted text, fallback to generic description
            info['desc'] = job_data.get('description_text') or job_data.get('description', '')
            
            # Clean up date format (remove time part)
            raw_date = job_data.get('date_posted', '')
            info['date'] = raw_date.split('T')[0] if raw_date else ''
            
            # Extended metadata
            info['c_url'] = job_data.get('organization_url', '')
            info['c_ind'] = job_data.get('linkedin_org_industry', '')
            info['c_slogan'] = job_data.get('linkedin_org_slogan', '')
            info['c_desc'] = job_data.get('linkedin_org_description', '')

        elif source == "GOOGLE":
            # --- Schema B: Google Jobs (JSearch) API ---    
            info['title'] = job_data.get('job_title', 'Unknown Role')
            info['company'] = job_data.get('employer_name', 'Unknown Company')

            # Google returns components
            info['location'] = f"{job_data.get('job_city','')}, {job_data.get('job_country','')}"
            info['url'] = job_data.get('job_apply_link', '')
            info['desc'] = job_data.get('job_description', '')
            raw_date = job_data.get('date_posted', '')
            info['date'] = raw_date.split('T')[0] if raw_date else ''

            info['c_url'] = job_data.get('employer_website', '')

            # JSearch is less rich in company metadata
            info['c_ind'] = "N/A"
            info['c_slogan'] = "N/A"
            info['c_desc'] = "N/A"
            
        return info

    def _save_to_markdown(self, info, source, index):
        """
        Generates a clean Markdown file for a job.
        This format is optimized for the CV Maker Agent to read.
        """
        clean_desc = self._clean_description(info['desc'])
        clean_c_desc = self._clean_description(info['c_desc'])
        safe_date = datetime.now().strftime("%Y-%m-%d")

        md_content = f"""==================================================
JOB TITLE: {info['title']}
LOCATION:  {info['location']}
COMPANY:   {info['company']}
SOURCE:    {source} API
DATE:      {safe_date} (Posted: {info['date']})
LINK:      {info['url']}
==================================================

### ABOUT THE JOB / JOB DESCRIPTION

{clean_desc}


-----
### COMPANY INFORMATION

**Company:** {info['company']}
**Website:** {info['c_url']}
**Industry:** {info['c_ind']}
**Slogan:** {info['c_slogan']}

**About Company:**
{clean_c_desc}
"""

        safe_title = self._sanitize(info['title'])
        safe_comp = self._sanitize(info['company'])
        date_str = datetime.now().strftime("%Y%m%d")

        # Generate filename: jd_{source}_{title}_{company}_{date}_{seq}.md
        filename = f"jd_{source.lower()}_{safe_title}_{safe_comp}_{date_str}_{index:03d}.md"
        
        try:
            with open(os.path.join(self.jd_dir, filename), "w", encoding="utf-8") as f:
                f.write(md_content)
            return filename
        except Exception as e:
            logger.error(f"Save markdown failed: {e}")
            return None

    def _save_url_list(self, jobs_info_list, keyword, engine):
        """
        Export Option: Saves a lightweight TXT list of URLs.
        """
        if not jobs_info_list: return None
        
        safe_kw = self._sanitize(keyword)
        date_str = datetime.now().strftime("%Y%m%d")
        # 生成 4位 uuid
        uid = uuid.uuid4().hex[:4]
        
        filename = f"url_{safe_kw}_{engine}_{date_str}_{uid}.txt"
        filepath = os.path.join(self.url_dir, filename)
        
        lines = []
        lines.append(f"# Search: {keyword} via {engine.upper()}")
        lines.append(f"# Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"# Found: {len(jobs_info_list)} jobs")
        lines.append("-" * 50)
        
        for job in jobs_info_list:
            lines.append(f"Title: {job['title']}")
            lines.append(f"Company: {job['company']}")
            lines.append(f"URL: {job['url']}")
            lines.append("") # 空行
            
        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            return filename
        except Exception as e:
            logger.error(f"Save URL list failed: {e}")
            return None

    # ==========================================
    # Main Execution Entry Point
    # ==========================================

    def fetch_jobs_unified(self, keyword, location_code, period, engine, export_type="BOTH"):
        """
        The primary method called by the Agent Tool.
        Orchestrates the search, download, and save process.
        
        Args:
            keyword: Job role (e.g. "TM1 Developer")
            location_code: ISO code (e.g. 'au', 'us')
            period: Time range ('today', 'week', 'month')
            engine: 'linkedin' or 'google'
            export_type: 'JD', 'URL', or 'BOTH'
        """
        engine = engine.lower() if engine else self.default_engine
        export_type = export_type.upper()
        
        # 1. Workspace Prep: Archive old files based on export mode
        if export_type in ["JD", "BOTH"]:
            self._archive_directory(self.jd_dir)
        if export_type in ["URL", "BOTH"]:
            self._archive_directory(self.url_dir)
            
        # 2. Fetching Phase (Collect data first)
        all_found_jobs = [] # 存放提取后的 info 字典
        
        if "linkedin" in engine:
            # === Engine A: LinkedIn ===
            country_name = self._get_country_name(location_code)

            # Date filter logic
            now = datetime.utcnow()
            delta = timedelta(days=30)
            if period == 'today': delta = timedelta(days=1)
            elif period == '3days': delta = timedelta(days=3)
            elif period == 'week': delta = timedelta(days=7)
            date_filter = (now - delta).strftime('%Y-%m-%dT%H:%M:%S')
            
            # Pagination Config
            try:
                limit = int(self.client.config.get('RapidAPI', 'linkedin_limit', fallback='10'))
                max_rec = int(self.client.config.get('RapidAPI', 'linkedin_max_records', fallback='20'))
            except: limit=10; max_rec=20
            
            offset = 0
            logger.info(f"Starting LinkedIn Fetch: Max {max_rec}, Loc={country_name}")
            
            # Paging Loop
            while len(all_found_jobs) < max_rec:
                batch = self.client.search_linkedin(keyword, country_name, date_filter, start=offset)
                if not batch: break
                
                for job in batch:
                    # Normalize immediately
                    info = self._extract_job_info(job, "LINKEDIN")
                    all_found_jobs.append(info)
                    if len(all_found_jobs) >= max_rec: break
                
                offset += limit
                # Stop if fewer results returned than limit (End of results)
                if len(batch) < limit: break

        elif "google" in engine:
            # === Engine B: Google Jobs (JSearch) ===
            api_date = period if period in ['today', '3days', 'week', 'month'] else 'month'
            try:
                max_pages = int(self.client.config.get('RapidAPI', 'google_max_pages', fallback='1'))
            except: max_pages = 1
            
            current_page = 1
            while current_page <= max_pages:
                batch = self.client.search_google(keyword, location_code, api_date, page=current_page)
                if not batch: break
                
                for job in batch:
                    info = self._extract_job_info(job, "GOOGLE")
                    all_found_jobs.append(info)
                current_page += 1
        else:
            return f"Error: Unknown engine '{engine}'"

        if not all_found_jobs:
            return "No jobs found via API."

        # 3. Saving Phase (Based on user preference)
        msg_lines = [f"✅ Fetched {len(all_found_jobs)} jobs via {engine.upper()}."]
        
        # Option A: Generate JD files (Standard Workflow)
        if export_type in ["JD", "BOTH"]:
            saved_mds = []
            for i, info in enumerate(all_found_jobs):
                fname = self._save_to_markdown(info, engine.upper(), i)
                if fname: saved_mds.append(fname)
            msg_lines.append(f"- Saved {len(saved_mds)} Markdown files to '{self.jd_dir}'")

        # Option B: Save URL List
        if export_type in ["URL", "BOTH"]:
            url_fname = self._save_url_list(all_found_jobs, keyword, engine.upper())
            if url_fname:
                msg_lines.append(f"- Saved URL list to '{self.url_dir}/{url_fname}'")

        return "\n".join(msg_lines)


