import random
import json
import time
import logging
import os
import requests
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List, Any, Tuple

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Set up logging
def setup_logging():
    """Set up logging configuration."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler("scraper.log"),
            logging.StreamHandler()
        ]
    )
    return logging.getLogger("roberthalf_scraper")

logger = setup_logging()

def get_random_user_agent() -> str:
    """Return a random user agent from a pool of common ones."""
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/133.0.2782.0',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0'
    ]
    return random.choice(user_agents)

def add_human_delay(min_seconds: float = 1, max_seconds: float = 3) -> None:
    """Add a random delay to mimic human behavior."""
    delay = random.uniform(min_seconds, max_seconds)
    logger.debug(f"Adding human delay of {delay:.2f} seconds")
    time.sleep(delay)

def get_proxy_config() -> Optional[Dict[str, str]]:
    """
    Creates proxy configuration based on environment variables.
    Returns None if proxy is disabled or not properly configured.
    """
    # Check if proxy is enabled
    use_proxy = os.getenv('USE_PROXY', 'false').lower() == 'true'
    if not use_proxy:
        logger.info("Proxy is disabled")
        return None

    # Get proxy configuration
    proxy_server = os.getenv('PROXY_SERVER')
    proxy_auth = os.getenv('PROXY_AUTH')
    proxy_bypass = os.getenv('PROXY_BYPASS', '*.iproyal.com')

    # Validate proxy configuration
    if not proxy_server or not proxy_auth:
        logger.warning("Proxy configuration is incomplete")
        return None

    try:
        username, password = proxy_auth.split(':')
        return {
            "server": f"https://{proxy_server}",
            "username": username,
            "password": password,
            "bypass": proxy_bypass
        }
    except Exception as e:
        logger.error(f"Error creating proxy configuration: {e}")
        return None

def save_session_cookies(cookies: Dict[str, str], filename: str = 'session_cookies.json') -> None:
    """Save session cookies to a file."""
    try:
        with open(filename, 'w') as f:
            json.dump(cookies, f)
        logger.info(f"Session cookies saved to {filename}")
    except Exception as e:
        logger.error(f"Failed to save session cookies: {e}")

def load_session_cookies(filename: str = 'session_cookies.json') -> Optional[Dict[str, str]]:
    """Load session cookies from a file if it exists."""
    try:
        if not Path(filename).exists():
            logger.info(f"Cookies file {filename} not found")
            return None
            
        with open(filename, 'r') as f:
            cookies = json.load(f)
        
        logger.info(f"Loaded session cookies from {filename}")
        return cookies
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"Could not load session cookies: {e}")
        return None

def login_and_get_cookies(username: str, password: str) -> Tuple[Optional[Dict[str, str]], str]:
    """Handle the login process and return required cookies."""
    logger.info("Starting login process")
    user_agent = get_random_user_agent()
    
    with sync_playwright() as p:
        # Browser configuration
        proxy_config = get_proxy_config()
        
        logger.debug(f"Launching browser with user agent: {user_agent}")
        browser = p.chromium.launch(
            proxy=proxy_config,
            headless=True
        )
        
        try:
            context = browser.new_context(
                proxy=proxy_config,
                viewport={'width': 1920, 'height': 1080},
                user_agent=user_agent
            )
            
            page = context.new_page()
            
            # Navigate to login page
            login_url = "https://online.roberthalf.com/s/login?app=0sp3w000001UJH5&c=US&d=en_US&language=en_US&redirect=false"
            logger.info("Navigating to login page...")
            page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
            add_human_delay(2, 4)
            
            # Login
            logger.debug("Filling in username")
            username_field = page.locator('[data-id="username"] input')
            username_field.wait_for(state="visible", timeout=10000)
            username_field.fill(username)
            add_human_delay()
            
            logger.debug("Filling in password")
            password_field = page.locator('[data-id="password"] input')
            password_field.wait_for(state="visible", timeout=10000)
            password_field.fill(password)
            add_human_delay()
            
            logger.debug("Clicking sign in button")
            sign_in_button = page.locator('rhcl-button[data-id="signIn"]')
            sign_in_button.wait_for(state="visible", timeout=10000)
            sign_in_button.click()
            
            # Wait for navigation and cookies to be set
            logger.info("Waiting for navigation after login")
            page.wait_for_load_state("networkidle", timeout=30000)
            
            # Get all cookies
            cookies = context.cookies()
            
            # Convert cookies to dictionary format for requests
            cookie_dict = {cookie['name']: cookie['value'] for cookie in cookies}
            
            logger.info("Login successful, cookies obtained")
            return cookie_dict, user_agent
            
        except Exception as e:
            logger.error(f"Error during login: {e}")
            return None, user_agent
        finally:
            browser.close()

def validate_session(cookies: Dict[str, str], user_agent: str) -> bool:
    """Validate if the session cookies are still valid."""
    logger.info("Validating session cookies")
    url = 'https://www.roberthalf.com/bin/jobSearchServlet'
    
    headers = {
        'accept': 'application/json, text/plain, */*',
        'accept-language': 'en-US,en;q=0.9',
        'content-type': 'application/json',
        'origin': 'https://www.roberthalf.com',
        'referer': 'https://www.roberthalf.com/us/en/jobs',
        'user-agent': user_agent
    }
    
    payload = {
        "country": "us",
        "keywords": "",
        "location": "",
        "distance": "50",
        "pagenumber": 1,
        "pagesize": 1,
    }
    
    try:
        response = requests.post(url, headers=headers, cookies=cookies, json=payload)
        response.raise_for_status()
        logger.info("Session is valid")
        return True
    except Exception as e:
        logger.warning(f"Session validation failed: {e}")
        return False

def get_or_refresh_session() -> Tuple[Dict[str, str], str]:
    """Get existing session or create a new one if needed."""
    # Try to load existing session
    cookies = load_session_cookies()
    user_agent = get_random_user_agent()
    
    if cookies and validate_session(cookies, user_agent):
        logger.info("Using existing session")
        return cookies, user_agent
    
    # Session invalid or not found, get a new one
    logger.info("Creating new session")
    username = os.getenv('ROBERTHALF_USERNAME')
    password = os.getenv('ROBERTHALF_PASSWORD')
    
    if not username or not password:
        raise ValueError("Please set ROBERTHALF_USERNAME and ROBERTHALF_PASSWORD in your .env file")
    
    cookies, user_agent = login_and_get_cookies(username, password)
    
    if not cookies:
        raise ValueError("Failed to obtain session cookies")
    
    # Save new session
    save_session_cookies(cookies)
    return cookies, user_agent

def fetch_jobs(cookies: Dict[str, str], user_agent: str, page_number: int = 1) -> Optional[Dict[str, Any]]:
    """Fetch jobs using the API directly."""
    url = 'https://www.roberthalf.com/bin/jobSearchServlet'
    
    headers = {
        'accept': 'application/json, text/plain, */*',
        'accept-language': 'en-US,en;q=0.9',
        'content-type': 'application/json',
        'origin': 'https://www.roberthalf.com',
        'referer': 'https://www.roberthalf.com/us/en/jobs',
        'user-agent': user_agent
    }
    
    payload = {
        "country": "us",
        "keywords": "",
        "location": "",
        "distance": "50",
        "remote": "No",
        "remoteText": "",
        "languagecodes": [],
        "source": ["Salesforce"],
        "city": [],
        "emptype": [],
        "lobid": ["RHT"],
        "jobtype": "",
        "postedwithin": "PAST_24_HOURS",
        "timetype": "",
        "pagesize": 25,
        "pagenumber": page_number,
        "sortby": "RELEVANCE_DESC",
        "mode": "",
        "payratemin": 0,
        "includedoe": ""
    }
    
    try:
        logger.info(f"Fetching jobs page {page_number}")
        response = requests.post(url, headers=headers, cookies=cookies, json=payload)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Error fetching jobs: {e}")
        return None

def fetch_with_retry(cookies: Dict[str, str], user_agent: str, page_number: int, max_retries: int = 3) -> Optional[Dict[str, Any]]:
    """Fetch jobs with exponential backoff retry logic."""
    for attempt in range(max_retries):
        try:
            return fetch_jobs(cookies, user_agent, page_number)
        except Exception as e:
            wait_time = 2 ** attempt * 5  # 5, 10, 20 seconds
            logger.warning(f"Attempt {attempt+1} failed: {e}. Retrying in {wait_time} seconds...")
            time.sleep(wait_time)
    
    # All retries failed
    logger.error("All retry attempts failed.")
    return None

def filter_texas_jobs(jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filter jobs to only include Texas positions."""
    texas_jobs = [job for job in jobs if job.get('stateprovince') == 'TX']
    logger.info(f"Filtered {len(texas_jobs)} Texas jobs from {len(jobs)} total jobs")
    return texas_jobs

def save_jobs(jobs: Dict[str, Any], filename: str) -> None:
    """Save jobs to a JSON file."""
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(jobs, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved {len(jobs.get('jobs', []))} jobs to {filename}")

def scrape_roberthalf_jobs() -> None:
    """Main function to scrape Robert Half jobs."""
    try:
        # Get or refresh session
        cookies, user_agent = get_or_refresh_session()
        
        all_texas_jobs = []
        page_number = 1
        total_jobs = None
        
        while True:
            logger.info(f"Processing page {page_number}")
            response_data = fetch_with_retry(cookies, user_agent, page_number)
            
            if not response_data:
                logger.error("Failed to fetch jobs. Exiting.")
                break
                
            if total_jobs is None:
                total_jobs = int(response_data.get('found', 0))
                logger.info(f"Total jobs found: {total_jobs}")
            
            # Get jobs from response and filter for Texas
            jobs = response_data.get('jobs', [])
            texas_jobs = filter_texas_jobs(jobs)
            all_texas_jobs.extend(texas_jobs)
            
            logger.info(f"Found {len(texas_jobs)} Texas jobs on page {page_number}")
            
            # Check if we need to fetch more pages
            if len(jobs) < 25:  # Less than page size means last page
                logger.info("Reached last page")
                break
                
            page_number += 1
            # Add variable delay between pages to be nice to the server
            delay = random.uniform(5, 15)
            logger.info(f"Waiting {delay:.2f} seconds before next page")
            time.sleep(delay)
        
        # Save results
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = f"roberthalf_texas_jobs_{timestamp}.json"
        
        results_data = {
            "jobs": all_texas_jobs,
            "timestamp": timestamp,
            "total_texas_jobs": len(all_texas_jobs),
            "total_jobs_found": total_jobs,
            "status": "Completed"
        }
        
        save_jobs(results_data, output_file)
        logger.info(f"Scraping completed. Found {len(all_texas_jobs)} Texas jobs out of {total_jobs} total jobs.")
        
    except Exception as e:
        logger.error(f"Error in scrape_roberthalf_jobs: {e}")

if __name__ == "__main__":
    scrape_roberthalf_jobs()