"""
Project: Joey - The Voice-Enabled End-to-End Career Copilot
File: tools/rapid_api_client.py
Description: 
    This module acts as the low-level HTTP client/driver for external Job APIs.
    It encapsulates the logic for:
    1. Authentication: Injecting RapidAPI keys into headers.
    2. Request Formatting: Constructing engine-specific query parameters.
    3. Error Handling: Catching network exceptions to prevent system crashes.
    
    Supported Engines:
    - LinkedIn Job Search API
    - JSearch (Google Jobs) API
"""

import os
import requests
import logging
import configparser

logger = logging.getLogger("RapidAPIClient")

import os
import logging
import requests
import configparser
import time

logger = logging.getLogger("RapidAPIClient")

class RapidAPIClient:
    """
    Wrapper class for RapidAPI interactions. 
    Handles the specific nuances of different job search API providers.
    """
    
    # [Debug Flag] Set to True to use static mock data (saves API quota during dev)
    MOCK_MODE = False

    def __init__(self):
        """Initialize client and load credentials."""
        self.api_key = os.getenv("RAPIDAPI_KEY")
        self.config = self._load_config()

    def _load_config(self):
        """Loads settings.ini for API host endpoints and limits."""
        config = configparser.ConfigParser()
        config.read("settings.ini", encoding='utf-8')
        return config

    def search_linkedin(self, keyword, location_name, date_filter_timestamp, start=0):
        """
        Executes a search against the 'LinkedIn Job Search' API.
        
        Args:
            keyword: Job title or skill (e.g., "TM1 Developer").
            location_name: Full country name (e.g., "Australia").
            date_filter_timestamp: UTC format 'YYYY-MM-DDTHH:MM:SS'.
            start: Pagination offset (0, 10, 20...).
            
        Returns:
            list: A list of job dictionaries (raw API response).
        """
   
        # --- Mock Mode Logic (For Testing) ---
        if self.MOCK_MODE:
            time.sleep(1) # Simulate network latency
            limit = self.config.get('RapidAPI', 'linkedin_limit', fallback='10')
            logger.info(f"LINKEDIN API Call (MOCK): Title={keyword}, Loc={location_name}, Offset={start}")
            
            # Simulate pagination end
            if int(start) >= 20: return []
            
            # Return dummy data schema
            return [
                {
                    "title": f"Mock {keyword} Role (Offset {start})",
                    "organization": "Mock Company",
                    "locations_derived": [location_name],
                    "url": "https://linkedin.com/mock-job",
                    "description_text": "This is a mock JD...",
                    "date_posted": "2025-01-01T12:00:00"
                }
            ]

        # --- Real API Execution ---
        if not self.api_key:
            logger.error("RAPIDAPI_KEY not found.")
            return []

        host = self.config['RapidAPI']['host_linkedin']
        limit = self.config.get('RapidAPI', 'linkedin_limit', fallback='10')
        url = f"https://{host}/active-jb-7d"

        # [Critical Formatting] 
        # This specific API requires parameter values to be wrapped in double quotes.
        # e.g., title_filter="TM1 Developer" -> sent as '"TM1 Developer"'
        title_param = f'"{keyword}"'
        location_param = f'"{location_name}"'

        querystring = {
            "title_filter": title_param,
            "location_filter": location_param,
            "limit": str(limit),
            "offset": str(start),
            "description_type": "text", # Request plain text description (HTML free)
            "date_filter": date_filter_timestamp
        }

        headers = {
            "x-rapidapi-key": self.api_key,
            "x-rapidapi-host": host
        }

        try:
            logger.info(f"LINKEDIN API Call: Title={title_param}, Loc={location_param}, Offset={start}")
            response = requests.get(url, headers=headers, params=querystring)
            response.raise_for_status()
            data = response.json()
            # Ensure we always return a list, even if empty
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"LinkedIn API Request Failed: {e}")
            # Optional: Log response text for debugging 403/500 errors
            # if 'response' in locals() and response.text: logger.error(f"API Response: {response.text}")
            return []


    def search_google(self, keyword, country_code, date_posted, page=1):
        """
        Executes a search against the 'JSearch' (Google Jobs) API.
        
        Args:
            keyword: Search query.
            country_code: ISO code (e.g., 'us', 'au').
            date_posted: 'all', 'today', '3days', 'week', 'month'.
            page: Page number (1, 2, 3...).
        """
        
        # --- Mock Mode Logic ---
        if self.MOCK_MODE:
            logger.info(f"GOOGLE API Call (MOCK): Query={keyword}, Page={page}")
            time.sleep(1)
            if int(page) > 2: return []
            return [{
                "job_title": f"Mock Google Job (Page {page})",
                "employer_name": "Google Mock Corp",
                "job_city": "Remote",
                "job_country": country_code,
                "job_description": "Mock description..."
            }]

        # --- Real API Execution ---
        if not self.api_key:
            logger.error("RAPIDAPI_KEY not found.")
            return []

        host = self.config['RapidAPI']['host_google']
        num_pages = self.config.get('RapidAPI', 'google_num_pages', fallback='1')
        url = f"https://{host}/search"

        # JSearch behaves better when query includes the keyword directly
        full_query = f"{keyword}" 

        querystring = {
            "query": full_query,
            "page": str(page),
            "num_pages": str(num_pages),
            "country": country_code.lower(), 
            "date_posted": date_posted 
        }

        headers = {
            "x-rapidapi-key": self.api_key,
            "x-rapidapi-host": host
        }

        try:
            logger.info(f"GOOGLE API Call: Query={full_query}, Country={country_code}, Page={page}")
            response = requests.get(url, headers=headers, params=querystring)
            response.raise_for_status()
            data = response.json()
            # JSearch returns results wrapped in a 'data' key
            return data.get('data', [])
        except Exception as e:
            logger.error(f"Google Jobs API Request Failed: {e}")
            return []