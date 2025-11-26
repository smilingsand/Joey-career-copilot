import os
import logging
import asyncio
import configparser
import sys
from dotenv import load_dotenv

from services.job_scout_service import JobScoutService

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("google_genai").setLevel(logging.WARNING)
logging.getLogger("JobScoutService").setLevel(logging.INFO)
logging.getLogger("RapidAPIClient").setLevel(logging.INFO)

def load_settings(ini_path="settings.ini"):
    if not os.path.exists(ini_path):
        print(f"Error: {ini_path} not found.")
        return None
    config = configparser.ConfigParser()
    config.read(ini_path, encoding='utf-8')
    return config

async def main():
    load_dotenv()
    if not os.getenv("RAPIDAPI_KEY"):
        print("❌ Error: RAPIDAPI_KEY not found in environment.")
        return

    settings = load_settings()
    if not settings: return

    jd_dir = settings['Paths']['input_dir']
    url_dir = settings['Paths']['url_dir']
    default_engine = settings['Search']['default_engine'] # linkedin
    max_results = settings['Search']['max_results']

    scout_service = JobScoutService(jd_dir, url_dir, default_engine, max_results)

    options='''
\n=== JOB SCOUT (RAPID API EDITION) ===
1. Find & Download Jobs
2. Exit
Select Option: '''

    
    while True:
        choice = input(options).strip()
        
        if choice == '1':
            # 1. Select Engine (支持简写)
            print(f"\nSelect Engine (Default: {default_engine}):")
            print("  [L] LinkedIn")
            print("  [G] Google Jobs")
            eng_input = input("Engine: ").strip().lower()
            
            if eng_input.startswith('g'):
                engine = 'google'
            elif eng_input.startswith('l'):
                engine = 'linkedin'
            else:
                engine = default_engine
            
            # 2. Keyword
            keyword = input("Role Keyword (e.g. TM1 Developer): ").strip()
            if not keyword: 
                print("Keyword required.")
                continue
            
            # 3. Location
            loc_input = input("Location Code (e.g. au, us, cn, de) [Default: au]: ").strip().lower()
            location_code = loc_input if loc_input else "au"
            
            # 4. Period (支持简写)
            period_map = {'t': 'today', '3': '3days', 'w': 'week', 'm': 'month'}
            period_input = input("Period Options ( [T]oday, [3]days, [W]eek, [M]onth ) [Default: Month]:").strip().lower()
            if period_input in period_map: period = period_map[period_input]
            elif period_input in period_map.values(): period = period_input
            else: period = "month"
            
            # 5. [新增] Export Type
            exp_input = input("Export ( [J]D, [U]RL, [B]oth ) [Default: Both]:").strip().lower()
            if exp_input.startswith('j'): export_type = 'JD'
            elif exp_input.startswith('u'): export_type = 'URL'
            else: export_type = 'BOTH' # Default

            # Execute
            print(f"\n🚀 Launching Scout: {engine.upper()} | {keyword} | {location_code} | {period} | Export: {export_type}")
            # [修改] 传递 export_type
            result = scout_service.fetch_jobs_unified(keyword, location_code, period, engine, export_type)
            print(result)
            
        elif choice == '2':
            break

if __name__ == "__main__":
    asyncio.run(main())


