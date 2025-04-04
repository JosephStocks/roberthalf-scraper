import random
import json
import time
import logging
import os
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional, List, Any, Tuple

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError

from config_loader import load_prod_config
from utils import get_proxy_config

load_prod_config()

# --- Constants ---
# Define base directories
LOG_DIR = Path("logs")
SESSION_DIR = Path(".session")
OUTPUT_DIR = Path("output") # Add output dir constant for consistency

# Create directories if they don't exist early on (optional, could also do it lazily)
LOG_DIR.mkdir(parents=True, exist_ok=True)
SESSION_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True) # Ensure output dir is also created here

# Update SESSION_FILE default to be inside SESSION_DIR
DEFAULT_SESSION_FILENAME = "session_data.json"
SESSION_FILE_PATH = SESSION_DIR / os.getenv('SESSION_FILE', DEFAULT_SESSION_FILENAME)

SAVE_SESSION = os.getenv('SAVE_SESSION', 'true').lower() == 'true' # Use updated env var name from review
# SESSION_FILE = os.getenv('SESSION_COOKIES_FILE', 'session_data.json') # Rename to reflect it holds more than cookies # OLD - Remove direct use of filename string
SESSION_MAX_AGE_HOURS = int(os.getenv('SESSION_MAX_AGE_HOURS', '12'))
FILTER_STATE = os.getenv('FILTER_STATE', 'TX')
JOB_POST_PERIOD = os.getenv('JOB_POST_PERIOD', 'PAST_24_HOURS') # e.g., PAST_24_HOURS, PAST_3_DAYS, PAST_WEEK, ALL
HEADLESS_BROWSER = os.getenv('HEADLESS_BROWSER', 'true').lower() == 'true' # Use updated env var name from review
ROTATE_USER_AGENT = os.getenv('ROTATE_USER_AGENT', 'true').lower() == 'true'
DEFAULT_USER_AGENT = os.getenv('DEFAULT_USER_AGENT', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36') # Use updated env var name
REQUEST_DELAY_SECONDS = float(os.getenv('REQUEST_DELAY_SECONDS', '2')) # Use updated env var name
PAGE_DELAY_MIN = float(os.getenv('PAGE_DELAY_MIN', '5'))
PAGE_DELAY_MAX = float(os.getenv('PAGE_DELAY_MAX', '15'))
MAX_RETRIES = int(os.getenv('MAX_RETRIES', '3'))
BROWSER_TIMEOUT_MS = int(os.getenv('BROWSER_TIMEOUT_MS', '60000')) # Use updated env var name
REQUEST_TIMEOUT_SECONDS = int(os.getenv('REQUEST_TIMEOUT_SECONDS', '30')) # Use updated env var name

# --- Logging Setup ---
def setup_logging():
    """Set up logging configuration to log to 'logs/scraper.log'."""
    log_file_path = LOG_DIR / "scraper.log"
    
    # Ensure log directory exists (redundant if created above, but safe)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"WARNING: Could not create log directory {LOG_DIR}: {e}. Logging to file might fail.") # Use print as logger not fully setup yet

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s', # Added filename/lineno
        handlers=[
            logging.FileHandler(log_file_path, mode='w'), # Use the path object
            logging.StreamHandler()
        ]
    )
    # Quieter libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)
    # Use __name__ for the logger specific to this module
    return logging.getLogger(__name__) # Changed from "roberthalf_scraper" to __name__

logger = setup_logging()

# --- Helper Functions ---

def get_user_agent() -> str:
    """Return a random user agent or the default one based on config."""
    if not ROTATE_USER_AGENT:
        return DEFAULT_USER_AGENT

    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/133.0.2782.0',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0'
    ]
    return random.choice(user_agents)

def add_human_delay(min_seconds: float = 0.5, max_seconds: float = 1.5) -> None:
    """Add a random delay to mimic human behavior during browser interaction."""
    delay = random.uniform(min_seconds, max_seconds)
    logger.debug(f"Adding browser interaction delay of {delay:.2f} seconds")
    time.sleep(delay)

def save_session_data(cookies: List[Dict[str, Any]], user_agent: str, filename_path: Path = SESSION_FILE_PATH) -> None:
    """Save session cookies and user agent to the specified file path."""
    if not SAVE_SESSION:
        logger.info("Session saving is disabled.")
        return

    # Ensure the directory exists before writing (redundant if created early, but safe)
    try:
        filename_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.error(f"Failed to ensure session directory exists for {filename_path}: {e}")
        # Decide if we should stop or just warn
        # return # Option: Stop if directory cannot be created

    session_data = {
        'cookies': cookies,
        'user_agent': user_agent,
        'timestamp': datetime.now(timezone.utc).isoformat() # Store timestamp
    }
    try:
        with open(filename_path, 'w') as f: # Use the Path object directly
            json.dump(session_data, f, indent=2)
        logger.info(f"Session data (cookies & UA) saved to {filename_path.resolve()}")
    except Exception as e:
        logger.error(f"Failed to save session data to {filename_path.resolve()}: {e}")

def load_session_data(filename_path: Path = SESSION_FILE_PATH) -> Optional[Tuple[List[Dict[str, Any]], str]]:
    """
    Load session cookies and user agent from the specified file path if it exists and is not expired.
    Returns (cookies, user_agent) or None.
    """
    if not SAVE_SESSION:
        return None

    # Use the Path object directly
    if not filename_path.exists():
        logger.info(f"Session file {filename_path.resolve()} not found.")
        return None

    try:
        with open(filename_path, 'r') as f:
            session_data = json.load(f)

        saved_cookies = session_data.get('cookies')
        saved_user_agent = session_data.get('user_agent')
        saved_timestamp_str = session_data.get('timestamp')

        if not saved_cookies or not saved_user_agent or not saved_timestamp_str:
            logger.warning(f"Session file {filename_path.resolve()} is incomplete. Ignoring.")
            return None

        # Check session age
        saved_timestamp = datetime.fromisoformat(saved_timestamp_str)
        if datetime.now(timezone.utc) - saved_timestamp > timedelta(hours=SESSION_MAX_AGE_HOURS):
            logger.info(f"Session data in {filename_path.resolve()} has expired (older than {SESSION_MAX_AGE_HOURS} hours). Refreshing.")
            filename_path.unlink() # Delete expired session file
            return None

        logger.info(f"Loaded valid session data (cookies & UA) from {filename_path.resolve()}")
        return saved_cookies, saved_user_agent

    except (FileNotFoundError, json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning(f"Could not load or parse session data from {filename_path.resolve()}: {e}. Will create a new session.")
        if filename_path.exists():
            try:
                filename_path.unlink() # Delete corrupted file
            except OSError as unlink_err:
                logger.error(f"Failed to delete corrupted session file {filename_path.resolve()}: {unlink_err}")
        return None

def login_and_get_session() -> Tuple[Optional[List[Dict[str, Any]]], str]:
    """Handle the login process using Playwright and return cookies and user agent."""
    logger.info("Starting login process with Playwright")
    
    # Determine user agent for this session attempt
    session_user_agent = get_user_agent()
    logger.info(f"Using User Agent for login: {session_user_agent}")

    with sync_playwright() as p:
        proxy_config = get_proxy_config()
        browser = None
        context = None
        try:
            logger.debug(f"Launching browser (Headless: {HEADLESS_BROWSER})")
            browser = p.chromium.launch(
                proxy=proxy_config,
                headless=HEADLESS_BROWSER,
                timeout=BROWSER_TIMEOUT_MS
            )
            
            context = browser.new_context(
                proxy=proxy_config,
                viewport={'width': 1920, 'height': 1080},
                user_agent=session_user_agent,
                # Attempt to bypass bot detection
                java_script_enabled=True,
                accept_downloads=False,
                ignore_https_errors=True, # Can sometimes help with proxy/network issues
            )
            # Set navigation timeout after context creation
            context.set_default_navigation_timeout(BROWSER_TIMEOUT_MS)
            # Grant necessary permissions if any popups require them
            context.grant_permissions(['geolocation'])

            page = context.new_page()

            # Navigate to login page
            login_url = "https://online.roberthalf.com/s/login?app=0sp3w000001UJH5&c=US&d=en_US&language=en_US&redirect=false"
            logger.info(f"Navigating to login page: {login_url}")
            page.goto(login_url, wait_until="domcontentloaded", timeout=BROWSER_TIMEOUT_MS)
            add_human_delay(2, 4)

            # Get credentials securely
            username = os.getenv('ROBERTHALF_USERNAME')
            password = os.getenv('ROBERTHALF_PASSWORD')
            if not username or not password:
                logger.error("ROBERTHALF_USERNAME or ROBERTHALF_PASSWORD not set in environment.")
                raise ValueError("Missing login credentials in environment variables.")

            # --- Login Steps ---
            logger.debug("Waiting for username field")
            username_field = page.locator('[data-id="username"] input')
            username_field.wait_for(state="attached", timeout=15000) # Changed from "visible" to "attached"
            logger.debug("Filling in username")
            username_field.fill(username)
            add_human_delay()

            logger.debug("Waiting for password field")
            password_field = page.locator('[data-id="password"] input')
            password_field.wait_for(state="attached", timeout=10000) # Changed from "visible" to "attached"
            logger.debug("Filling in password")
            password_field.fill(password)
            add_human_delay()

            logger.debug("Waiting for sign in button")
            sign_in_button = page.locator('rhcl-button[data-id="signIn"]')
            sign_in_button.wait_for(state="attached", timeout=10000) # Changed from "enabled" to "attached"
            logger.debug("Clicking sign in button")
            sign_in_button.click()

            # Wait for navigation/login confirmation
            # Option 1: Wait for a specific element that appears after login
            # Option 2: Wait for URL change (if applicable)
            # Option 3: Wait for network idle (less reliable if background requests continue)
            logger.info("Waiting for post-login state (networkidle)...")
            try:
                # Check if login failed (e.g., error message appears)
                error_locator = page.locator('div[role="alert"]:visible, .login-error:visible') # Example selectors
                error_visible = error_locator.is_visible(timeout=5000) # Quick check
                if error_visible:
                     error_text = error_locator.first.text_content()
                     logger.error(f"Login failed. Detected error message: {error_text}")
                     return None, session_user_agent # Login failed

                page.wait_for_load_state("networkidle", timeout=BROWSER_TIMEOUT_MS)
                logger.info("Post-login network idle state reached.")

            except PlaywrightTimeoutError:
                logger.warning("Timeout waiting for network idle after login, proceeding cautiously.")
            except Exception as wait_err:
                 logger.warning(f"Error during post-login wait: {wait_err}")


            # Get cookies in Playwright format (list of dicts)
            cookies = context.cookies()
            if not cookies:
                logger.error("Failed to retrieve cookies after login attempt.")
                return None, session_user_agent

            logger.info(f"Login successful, {len(cookies)} cookies obtained.")
            return cookies, session_user_agent # Return cookies and the UA used

        except PlaywrightTimeoutError as te:
            logger.error(f"Timeout error during Playwright operation: {te}")
            # Capture screenshot on error if possible
            if 'page' in locals() and page:
                try:
                    page.screenshot(path="playwright_error_screenshot.png")
                    logger.info("Screenshot saved to playwright_error_screenshot.png")
                except Exception as ss_err:
                    logger.error(f"Failed to capture screenshot: {ss_err}")
            return None, session_user_agent
        except PlaywrightError as pe:
             logger.error(f"Playwright specific error during login: {pe}")
             return None, session_user_agent
        except Exception as e:
            logger.error(f"Unexpected error during login process: {e}")
            return None, session_user_agent
        finally:
            if context:
                context.close()
            if browser:
                browser.close()
            logger.debug("Playwright browser closed.")


def validate_session(cookies_list: List[Dict[str, Any]], user_agent: str) -> bool:
    """Validate if the session cookies are still valid using the API."""
    logger.info("Validating session cookies via API")
    url = 'https://www.roberthalf.com/bin/jobSearchServlet'

    # Convert Playwright cookies list to dict for requests
    cookie_dict = {cookie['name']: cookie['value'] for cookie in cookies_list}

    headers = {
        'accept': 'application/json, text/plain, */*',
        'accept-language': 'en-US,en;q=0.9',
        'content-type': 'application/json',
        'origin': 'https://www.roberthalf.com',
        'referer': 'https://www.roberthalf.com/us/en/jobs',
        'user-agent': user_agent # Use the specific user agent for this session
    }

    # Minimal payload for validation check
    payload = {
        "country": "us",
        "keywords": "",
        "location": "",
        "pagenumber": 1,
        "pagesize": 1, # Fetch only 1 job to validate
        "lobid": ["RHT"], # Important filter, likely needed for session scope
        "source": ["Salesforce"],
    }

    try:
        response = requests.post(url, headers=headers, cookies=cookie_dict, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
        
        logger.debug(f"Validation request status: {response.status_code}")
        # Check for successful status codes and potentially inspect response content if needed
        if response.status_code >= 200 and response.status_code < 300:
            # Simple check: did we get a JSON response back?
            try:
                response.json()
                logger.info("Session validation successful (API responded)")
                return True
            except json.JSONDecodeError:
                logger.warning("Session validation failed: API did not return valid JSON.")
                return False
        else:
            logger.warning(f"Session validation failed: Status code {response.status_code}, Response: {response.text[:200]}...") # Log start of response
            return False

    except requests.exceptions.RequestException as e:
        logger.warning(f"Session validation failed due to network error: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error during session validation: {e}")
        return False


def get_or_refresh_session() -> Tuple[List[Dict[str, Any]], str]:
    """Get existing session data or create a new one if needed."""
    loaded_data = load_session_data() # Uses SESSION_FILE_PATH by default

    if loaded_data:
        logger.info("Found existing session data, will try to use it")
        return loaded_data

    logger.info("No valid session data found. Creating new session.")

    # Get a new session
    cookies, user_agent = login_and_get_session()

    if not cookies or not user_agent:
        raise RuntimeError("Failed to obtain a valid session after login attempt.")

    # Save the new valid session data
    save_session_data(cookies, user_agent) # Uses SESSION_FILE_PATH by default
    return cookies, user_agent


def fetch_jobs(cookies_list: List[Dict[str, Any]], user_agent: str, page_number: int = 1) -> Optional[Dict[str, Any]]:
    """Fetch jobs using the API directly with the correct session data."""
    url = 'https://www.roberthalf.com/bin/jobSearchServlet'

    # Convert Playwright cookies list to dict for requests
    cookie_dict = {cookie['name']: cookie['value'] for cookie in cookies_list}

    headers = {
        'accept': 'application/json, text/plain, */*',
        'accept-language': 'en-US,en;q=0.9',
        'content-type': 'application/json',
        'origin': 'https://www.roberthalf.com',
        'referer': 'https://www.roberthalf.com/us/en/jobs',
        'user-agent': user_agent
    }

    # Construct payload based on config/needs
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
        "postedwithin": JOB_POST_PERIOD,
        "timetype": "",
        "pagesize": 25,
        "pagenumber": page_number,
        "sortby": "PUBLISHED_DATE_DESC",
        "mode": "",
        "payratemin": 0,
        "includedoe": ""
    }

    try:
        logger.info(f"Fetching jobs page {page_number} using session UA: {user_agent}")
        response = requests.post(url, headers=headers, cookies=cookie_dict, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        
        # Try to parse response as JSON
        try:
            data = response.json()
            return data
        except json.JSONDecodeError:
            logger.warning("Failed to parse API response as JSON. Session may be invalid.")
            return None
            
    except requests.exceptions.HTTPError as http_err:
        if response.status_code in (401, 403, 500):
            logger.warning("Session appears to be invalid or expired")
        else:
            logger.error(f"HTTP error fetching jobs page {page_number}: {http_err}")
        return None
    except requests.exceptions.RequestException as req_err:
        logger.error(f"Network error fetching jobs page {page_number}: {req_err}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error fetching jobs page {page_number}: {e}")
        return None

def fetch_with_retry(cookies_list: List[Dict[str, Any]], user_agent: str, page_number: int) -> Optional[Dict[str, Any]]:
    """Fetch jobs with exponential backoff retry logic."""
    base_wait_time = 5 # Initial wait time in seconds
    for attempt in range(MAX_RETRIES):
        result = fetch_jobs(cookies_list, user_agent, page_number)
        if result is not None:
            return result # Success

        # Failed, calculate wait time and retry
        wait_time = base_wait_time * (2 ** attempt) + random.uniform(0, base_wait_time) # Exponential backoff + jitter
        logger.warning(f"API fetch attempt {attempt + 1}/{MAX_RETRIES} failed for page {page_number}. Retrying in {wait_time:.2f} seconds...")
        time.sleep(wait_time)

    # All retries failed
    logger.error(f"All {MAX_RETRIES} retry attempts failed for page {page_number}.")
    return None

def filter_jobs_by_state(jobs: List[Dict[str, Any]], state_code: str) -> List[Dict[str, Any]]:
    """Filter jobs to only include positions in the specified state."""
    filtered_jobs = [job for job in jobs if job.get('stateprovince') == state_code]
    logger.info(f"Filtered {len(filtered_jobs)} {state_code} jobs from {len(jobs)} total jobs on page")
    return filtered_jobs

def save_job_results(jobs_list: List[Dict[str, Any]], total_found: int, filename_prefix: str = "roberthalf") -> None:
    """Save the final list of jobs to a JSON file inside the OUTPUT_DIR."""
    # Define the output directory using the constant
    output_dir = OUTPUT_DIR # Use the constant defined earlier

    # Ensure the output directory exists (redundant if created early, but safe)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Ensured output directory exists: {output_dir.resolve()}")
    except OSError as e:
        logger.error(f"Failed to create output directory {output_dir}: {e}")
        # Optionally decide whether to proceed

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Construct the filename and then the full path within the output directory
    filename = f"{filename_prefix}_{FILTER_STATE.lower()}_jobs_{timestamp}.json"
    output_file_path = output_dir / filename # Use the constant path object

    results_data = {
        "jobs": jobs_list,
        "timestamp": timestamp,
        f"total_{FILTER_STATE.lower()}_jobs_scraped": len(jobs_list),
        "total_jobs_found_in_period": total_found, # Total from API response metadata
        "job_post_period_filter": JOB_POST_PERIOD,
        "state_filter": FILTER_STATE,
        "status": "Completed"
    }

    try:
        with open(output_file_path, 'w', encoding='utf-8') as f:
            json.dump(results_data, f, indent=2, ensure_ascii=False)
        # Log the full path
        logger.info(f"Saved {len(jobs_list)} jobs to {output_file_path.resolve()}")
    except Exception as e:
        # Log the full path in case of error too
        logger.error(f"Failed to save job results to {output_file_path.resolve()}: {e}")

def scrape_roberthalf_jobs() -> None:
    """Main function to orchestrate the Robert Half job scraping."""
    logger.info("--- Starting Robert Half Job Scraper ---")
    start_time = time.time()

    try:
        # Get or refresh session (cookies + user_agent)
        session_cookies, session_user_agent = get_or_refresh_session() # Uses new session functions

        # Try to fetch first page
        response_data = fetch_with_retry(session_cookies, session_user_agent, 1)

        # If first page fails, try getting a new session
        if not response_data:
            logger.info("Initial fetch failed with existing session, getting new session")
            cookies, user_agent = login_and_get_session() # Get new session via login
            if not cookies or not user_agent:
                raise RuntimeError("Failed to obtain a valid session after fresh login attempt.")
            save_session_data(cookies, user_agent) # Save the newly obtained session
            session_cookies, session_user_agent = cookies, user_agent # Update variables
            
            response_data = fetch_with_retry(session_cookies, session_user_agent, 1)
            if not response_data:
                raise RuntimeError("Failed to fetch data even with newly obtained session")

        all_filtered_jobs = []
        page_number = 1
        total_jobs_api = None

        while True:
            logger.info(f"--- Processing Page {page_number} ---")

            # Fetch data for the current page with retries
            response_data = fetch_with_retry(session_cookies, session_user_agent, page_number)

            if not response_data:
                # Check if the session might be invalid here as well
                logger.warning(f"Fetch failed for page {page_number}. Trying to validate session.")
                is_valid = validate_session(session_cookies, session_user_agent)
                if not is_valid:
                    logger.error("Session became invalid during pagination. Stopping.")
                    # Optionally: could try re-logging in here, but for now, we stop.
                    # raise RuntimeError("Session became invalid during scraping") 
                    break 
                else:
                     logger.error(f"Session still seems valid, but failed to fetch page {page_number} after retries. Stopping.")
                     break # Stop if fetch fails for other reasons after retries


            # Extract total count on the first page
            if total_jobs_api is None:
                try:
                    total_jobs_api = int(response_data.get('found', 0))
                    logger.info(f"API reports {total_jobs_api} total jobs found for period '{JOB_POST_PERIOD}'")
                except (ValueError, TypeError):
                    logger.warning("Could not parse 'found' count from API response.")
                    total_jobs_api = -1 # Indicate unknown

            jobs_on_page = response_data.get('jobs', [])
            if not jobs_on_page:
                logger.info(f"No more jobs found on page {page_number}. Reached the end.")
                break # Exit loop if no jobs are returned

            logger.info(f"Received {len(jobs_on_page)} jobs on page {page_number}.")

            # Filter jobs by state
            state_jobs_on_page = filter_jobs_by_state(jobs_on_page, FILTER_STATE)
            all_filtered_jobs.extend(state_jobs_on_page)

            # Check if this was the last page based on page size
            # (The API might not always return exactly 'pagesize' even if more exist,
            # but if it returns less, it's definitely the end)
            if len(jobs_on_page) < 25: # Assuming page size is 25 (should ideally use constant or config)
                logger.info(f"Received less than assumed page size ({len(jobs_on_page)} < 25). Assuming last page.")
                break

            page_number += 1

            # Add variable delay between page requests
            page_delay = random.uniform(PAGE_DELAY_MIN, PAGE_DELAY_MAX)
            logger.info(f"Waiting {page_delay:.2f} seconds before fetching page {page_number}")
            time.sleep(page_delay)
            # Optional: Add general request delay too?
            time.sleep(REQUEST_DELAY_SECONDS) # Adding the general request delay as well


        # --- Save final results ---
        save_job_results(all_filtered_jobs, total_jobs_api if total_jobs_api is not None else 0)

    except RuntimeError as rt_err:
         logger.critical(f"Runtime error, likely session failure: {rt_err}")
    except ValueError as val_err:
        logger.critical(f"Configuration error: {val_err}")
    except Exception as e:
        logger.critical(f"An unexpected critical error occurred in the main process: {e}", exc_info=True) # Log traceback
    finally:
        end_time = time.time()
        logger.info(f"--- Robert Half Job Scraper Finished ---")
        logger.info(f"Total execution time: {end_time - start_time:.2f} seconds")


if __name__ == "__main__":
    scrape_roberthalf_jobs()