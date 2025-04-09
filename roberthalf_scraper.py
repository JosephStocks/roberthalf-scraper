# filename: roberthalf_scraper.py
import contextlib
import json
import logging
import random
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any  # Import Set for type hinting
from urllib.parse import urlparse

import pytz
import requests
from playwright.sync_api import (
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from config_loader import load_prod_config
from pushnotify import send_pushover_notification
from utils import get_proxy_config

# --- Constants ---
# Define base directories *before* logger setup uses them
LOG_DIR = Path("logs")
SESSION_DIR = Path(".session")
OUTPUT_DIR = Path("output")
DOCS_DIR = Path("docs")

# Create directories if they don't exist early on
LOG_DIR.mkdir(parents=True, exist_ok=True)
SESSION_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# --- Logging Setup ---
def setup_logging():
    """Set up logging configuration to log to 'logs/scraper.log'."""
    log_file_path = LOG_DIR / "scraper.log"

    logging.basicConfig(
        level=logging.INFO,
        format='%(levelname)s [%(filename)s:%(lineno)d] - %(message)s',
        handlers=[
            logging.FileHandler(log_file_path, mode='w'),
            logging.StreamHandler()
        ]
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)
    return logging.getLogger(__name__)

# Setup logger *after* LOG_DIR is defined
logger = setup_logging()

# Load config *after* logger is ready
# Ensure load_prod_config is called to get the config dictionary
config = load_prod_config()

# Update SESSION_FILE default to be inside SESSION_DIR
DEFAULT_SESSION_FILENAME = "session_data.json"
SESSION_FILE_PATH = SESSION_DIR / config.get('SESSION_FILE', DEFAULT_SESSION_FILENAME)

# Load configuration values with validation
SAVE_SESSION = config.get('SAVE_SESSION', True)
SESSION_MAX_AGE_HOURS = config.get('SESSION_MAX_AGE_HOURS', 12)
FILTER_STATE = config.get('FILTER_STATE', 'TX')
JOB_POST_PERIOD = config.get('JOB_POST_PERIOD', 'PAST_24_HOURS')
HEADLESS_BROWSER = config.get('HEADLESS_BROWSER', True)
ROTATE_USER_AGENT = config.get('ROTATE_USER_AGENT', False)
DEFAULT_USER_AGENT = config.get('DEFAULT_USER_AGENT', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36')
REQUEST_DELAY_SECONDS = config.get('REQUEST_DELAY_SECONDS', 2.0)
PAGE_DELAY_MIN = config.get('PAGE_DELAY_MIN', 5.0)
PAGE_DELAY_MAX = config.get('PAGE_DELAY_MAX', 15.0)
MAX_RETRIES = config.get('MAX_RETRIES', 3)
BROWSER_TIMEOUT_MS = config.get('BROWSER_TIMEOUT_MS', 60000)
REQUEST_TIMEOUT_SECONDS = config.get('REQUEST_TIMEOUT_SECONDS', 30)
TEST_MODE = config.get('TEST_MODE', False)
# --- GitHub Token ---
GITHUB_ACCESS_TOKEN = config.get('GITHUB_ACCESS_TOKEN', None) # Get token from config
GITHUB_PAGES_URL = config.get('GITHUB_PAGES_URL', None) # Get report URL from config


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

def save_session_data(cookies: list[dict[str, Any]], user_agent: str, filename_path: Path = SESSION_FILE_PATH) -> None:
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
        'timestamp': datetime.now(UTC).isoformat() # Store timestamp
    }
    try:
        with open(filename_path, 'w') as f: # Use the Path object directly
            json.dump(session_data, f, indent=2)
        logger.info(f"Session data (cookies & UA) saved to {filename_path.resolve()}")
    except Exception as e:
        logger.error(f"Failed to save session data to {filename_path.resolve()}: {e}")

def load_session_data(filename_path: Path = SESSION_FILE_PATH) -> tuple[list[dict[str, Any]], str] | None:
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
        with open(filename_path) as f:
            session_data = json.load(f)

        saved_cookies = session_data.get('cookies')
        saved_user_agent = session_data.get('user_agent')
        saved_timestamp_str = session_data.get('timestamp')

        if not saved_cookies or not saved_user_agent or not saved_timestamp_str:
            logger.warning(f"Session file {filename_path.resolve()} is incomplete. Ignoring.")
            return None

        # Check session age
        saved_timestamp = datetime.fromisoformat(saved_timestamp_str.replace('Z', '+00:00')) # Ensure timezone aware
        if datetime.now(UTC) - saved_timestamp > timedelta(hours=SESSION_MAX_AGE_HOURS):
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

def login_and_get_session() -> tuple[list[dict[str, Any]] | None, str]:
    """Handle the login process using Playwright and return cookies and user agent."""
    logger.info("Starting login process with Playwright")

    # Determine user agent for this session attempt
    session_user_agent = get_user_agent()
    logger.info(f"Using User Agent for login: {session_user_agent}")

    # Use the global config dictionary loaded earlier
    global config

    with sync_playwright() as p:
        proxy_config_dict = get_proxy_config() # Still uses utils for parsing env vars
        browser = None
        context = None
        try:
            logger.debug(f"Launching browser (Headless: {HEADLESS_BROWSER})")
            browser = p.chromium.launch(
                proxy=proxy_config_dict, # Pass the parsed dict here
                headless=HEADLESS_BROWSER,
                timeout=BROWSER_TIMEOUT_MS
            )

            context = browser.new_context(
                proxy=proxy_config_dict, # Pass the parsed dict here
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

            # Get credentials securely from loaded config
            username = config.get('ROBERTHALF_USERNAME')
            password = config.get('ROBERTHALF_PASSWORD')
            if not username or not password:
                logger.error("ROBERTHALF_USERNAME or ROBERTHALF_PASSWORD not found in loaded configuration.")
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
            logger.info("Waiting for post-login state (networkidle)...")
            try:
                # Check if login failed (e.g., error message appears)
                error_locator = page.locator('div[role="alert"]:visible, .login-error:visible') # Example selectors
                error_visible = error_locator.is_visible(timeout=5000) # Quick check
                if error_visible:
                     error_text = error_locator.first.text_content(timeout=2000) or "[Could not get error text]"
                     logger.error(f"Login failed. Detected error message: {error_text.strip()}")
                     # Capture screenshot on login error
                     try:
                        page.screenshot(path="playwright_login_error.png")
                        logger.info("Login error screenshot saved to playwright_login_error.png")
                     except Exception as ss_err:
                        logger.error(f"Failed to capture login error screenshot: {ss_err}")
                     return None, session_user_agent # Login failed

                page.wait_for_load_state("networkidle", timeout=BROWSER_TIMEOUT_MS)
                logger.info("Post-login network idle state reached.")

            except PlaywrightTimeoutError:
                logger.warning("Timeout waiting for network idle after login, proceeding cautiously.")
                # Maybe add a check here for a known post-login element?
                # Example: dashboard_element = page.locator('#dashboard-widget')
                # if not dashboard_element.is_visible(timeout=5000):
                #     logger.error("Network idle timed out AND dashboard element not found. Assuming login failed.")
                #     return None, session_user_agent
                # else:
                #     logger.info("Network idle timed out, but dashboard element found. Proceeding.")
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
            if 'page' in locals() and page:
                try:
                    page.screenshot(path="playwright_timeout_error.png")
                    logger.info("Timeout screenshot saved to playwright_timeout_error.png")
                except Exception as ss_err:
                    logger.error(f"Failed to capture timeout screenshot: {ss_err}")
            return None, session_user_agent
        except PlaywrightError as pe:
             logger.error(f"Playwright specific error during login: {pe}")
             return None, session_user_agent
        except ValueError as ve: # Catch missing credentials error
            logger.error(f"Configuration error during login: {ve}")
            raise # Re-raise config errors as they are critical
        except Exception as e:
            logger.error(f"Unexpected error during login process: {e}", exc_info=True)
            return None, session_user_agent
        finally:
            if context:
                context.close()
            if browser:
                browser.close()
            logger.debug("Playwright browser closed.")


def validate_session(cookies_list: list[dict[str, Any]], user_agent: str) -> bool:
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


def get_or_refresh_session() -> tuple[list[dict[str, Any]], str]:
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


def filter_jobs_by_state(jobs: list[dict[str, Any]], state_code: str) -> list[dict[str, Any]]:
    """Filter jobs to only include positions in the specified state or remote jobs."""
    # Debug log all jobs before filtering
    logger.debug(f"Before filtering - received {len(jobs)} jobs for {state_code} or remote check.")
    # Removed verbose per-job logging before filtering for brevity unless DEBUG level is on

    filtered_jobs = [
        job for job in jobs
        if (job.get('stateprovince') == state_code) or  # Jobs in target state
        (job.get('remote', '').lower() == 'yes' and job.get('country', '').lower() == 'us')  # US remote jobs
    ]

    # Count types of jobs for logging
    state_jobs_count = sum(1 for j in filtered_jobs if j.get('stateprovince') == state_code)
    remote_jobs_count = sum(1 for j in filtered_jobs if j.get('remote', '').lower() == 'yes' and j.get('country', '').lower() == 'us')

    # Debug log filtered jobs only if DEBUG enabled
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(f"After filtering - kept {len(filtered_jobs)} jobs:")
        for job in filtered_jobs:
            logger.debug(f"  Kept Job ID: {job.get('unique_job_number', 'N/A')}, "
                       f"Title: {job.get('jobtitle', 'N/A')}, "
                       f"State: {job.get('stateprovince', 'N/A')}, "
                       f"Remote: {job.get('remote', 'N/A')}, "
                       f"Country: {job.get('country', 'N/A')}")

    logger.info(f"Filtering kept {state_jobs_count} {state_code} jobs and {remote_jobs_count} US remote jobs from {len(jobs)} total on page")
    return filtered_jobs


def fetch_jobs(cookies_list: list[dict[str, Any]], user_agent: str, page_number: int = 1, is_remote: bool = False) -> dict[str, Any] | None:
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
        "remote": "yes" if is_remote else "No", # API expects "yes" or "No"
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
        logger.info(f"Fetching {'remote' if is_remote else 'local'} jobs page {page_number} using session UA: {user_agent}")
        # Use utils.get_proxy_config() to get proxy dict for requests if enabled
        proxy_config_dict = get_proxy_config()
        proxies = None
        if proxy_config_dict:
             # Format for requests library
             # Assumes http proxy, adjust if socks is needed
             server_url = proxy_config_dict['server']
             if proxy_config_dict.get('username') and proxy_config_dict.get('password'):
                 # Basic Auth format for requests
                 auth = f"{proxy_config_dict['username']}:{proxy_config_dict['password']}"
                 # Need to parse the server url to inject auth properly
                 parsed_url = urlparse(server_url)
                 proxy_url_with_auth = f"{parsed_url.scheme}://{auth}@{parsed_url.netloc}"
                 proxies = {"http": proxy_url_with_auth, "https": proxy_url_with_auth}
                 logger.debug(f"Using proxy for requests: {parsed_url.scheme}://****:****@{parsed_url.netloc}")
             else:
                 # Proxy without authentication
                 proxies = {"http": server_url, "https": server_url}
                 logger.debug(f"Using proxy for requests (no auth): {server_url}")

        response = requests.post(
            url,
            headers=headers,
            cookies=cookie_dict,
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
            proxies=proxies # Pass proxies dict to requests
            )
        response.raise_for_status()

        # Try to parse response as JSON
        try:
            data = response.json()
            return data
        except json.JSONDecodeError:
            logger.warning("Failed to parse API response as JSON. Session may be invalid.")
            return None

    except requests.exceptions.HTTPError as http_err:
        status_code = http_err.response.status_code
        if status_code in (401, 403):
            logger.warning(f"HTTP {status_code} error suggests session is invalid or expired.")
        elif status_code == 500:
             logger.warning(f"HTTP 500 Server Error fetching jobs page {page_number}. Body: {http_err.response.text[:200]}...")
        else:
            logger.error(f"HTTP error fetching jobs page {page_number}: {http_err}")
        return None
    except requests.exceptions.ProxyError as proxy_err:
        logger.error(f"Proxy error fetching jobs page {page_number}: {proxy_err}")
        return None
    except requests.exceptions.RequestException as req_err:
        logger.error(f"Network error fetching jobs page {page_number}: {req_err}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error fetching jobs page {page_number}: {e}", exc_info=True)
        return None

def fetch_with_retry(cookies_list: list[dict[str, Any]], user_agent: str, page_number: int, is_remote: bool = False) -> dict[str, Any] | None:
    """Fetch jobs with exponential backoff retry logic."""
    base_wait_time = 5 # Initial wait time in seconds
    for attempt in range(MAX_RETRIES):
        result = fetch_jobs(cookies_list, user_agent, page_number, is_remote)
        if result is not None:
            return result # Success

        # Failed, calculate wait time and retry
        wait_time = base_wait_time * (2 ** attempt) + random.uniform(0, base_wait_time) # Exponential backoff + jitter
        logger.warning(f"API fetch attempt {attempt + 1}/{MAX_RETRIES} failed for {'remote' if is_remote else 'local'} page {page_number}. Retrying in {wait_time:.2f} seconds...")
        time.sleep(wait_time)

    # All retries failed
    logger.error(f"All {MAX_RETRIES} retry attempts failed for {'remote' if is_remote else 'local'} page {page_number}.")
    return None

def _generate_html_report(
    jobs_list: list[dict[str, Any]],
    timestamp: str,
    total_found: int,
    state_filter: str,
    job_period: str,
    new_job_ids: set[str] # Added argument
) -> str:
    """Generates an HTML report string from the job list, highlighting new jobs."""
    num_tx_jobs = len([job for job in jobs_list if job.get('stateprovince') == state_filter])
    num_remote_jobs = len([job for job in jobs_list if job.get('remote', '').lower() == 'yes'])
    num_new_jobs = len(new_job_ids) # Count new jobs

    # Convert UTC timestamp to CST
    cst = pytz.timezone('America/Chicago')
    dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    cst_dt = dt.astimezone(cst)
    formatted_timestamp = cst_dt.strftime('%Y-%m-%d %H:%M:%S %Z')

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Robert Half Job Report ({state_filter}) - {timestamp}</title>
    <style>
        body {{ font-family: sans-serif; margin: 20px; }}
        h1 {{ color: #333; }}
        p {{ color: #555; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; vertical-align: top; }} /* Added vertical-align */
        th {{ background-color: #f2f2f2; }}
        tr:nth-child(even) {{ background-color: #f9f9f9; }}
        a {{ color: #007bff; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        .pay-rate {{ white-space: nowrap; }}
        .location {{ white-space: nowrap; }}
        details {{ margin: 10px 0; }}
        summary {{
            cursor: pointer;
            color: #555; /* Changed from #007bff to a subtle gray */
            padding: 6px; /* Slightly reduced padding */
            background-color: #f8f9fa;
            border: 1px solid #dee2e6;
            border-radius: 4px;
            font-size: 0.9rem; /* Reduced font size */
            font-weight: normal; /* Ensure normal weight */
        }}
        summary:hover {{
            background-color: #e9ecef;
            color: #333; /* Slightly darker on hover for feedback */
        }}
        .job-description {{
            padding: 15px;
            background-color: #fff;
            border: 1px solid #dee2e6;
            border-radius: 4px;
            margin-top: 8px;
        }}
        .description-row td {{
            padding: 0 8px;
            background-color: #fff; /* Ensure description background is white */
            border: none; /* Remove border for description cell */
            border-bottom: 1px solid #ddd; /* Keep bottom border for row separation */
        }}
        .description-row.new-job td {{ /* Style description row if job is new */
             background-color: #f0fff0; /* Light green */
        }}

        /* Styles for new job highlighting */
        .new-job > td {{ /* Apply background to cells of new job row */
             background-color: #f0fff0 !important; /* Light green, !important might be needed depending on other styles */
        }}
        .new-tag {{
            display: inline-block; /* Allows margin/padding */
            background-color: #28a745; /* Bootstrap success green */
            color: white;
            padding: 2px 6px;
            font-size: 0.75em;
            font-weight: bold;
            border-radius: 4px;
            margin-right: 5px;
            vertical-align: middle; /* Align with text */
        }}
    </style>
</head>
<body>
    <h1>Robert Half Job Report</h1>
    <p>Generated: {formatted_timestamp}</p>
    <p>Filters: State = {state_filter}, Posted Within = {job_period.replace('_', ' ')}</p>
    <p>Found {num_tx_jobs} jobs in {state_filter} and {num_remote_jobs} remote jobs (Total Unique: {len(jobs_list)}). Identified <span style="background-color: #f0fff0; padding: 1px 3px; border: 1px solid #ccc;">{num_new_jobs} New Jobs</span> since last report. API reported {total_found} total jobs matching period.</p>

    <table>
        <thead>
            <tr>
                <th>Title</th>
                <th>Location</th>
                <th>Pay Rate</th>
                <th>Job ID</th>
                <th>Posted Date</th>
            </tr>
        </thead>
        <tbody>
"""
    # Sort jobs by posted date descending, then title
    jobs_list.sort(key=lambda x: (x.get('date_posted', '1970-01-01'), x.get('jobtitle', '')), reverse=True)

    for job in jobs_list:
        title = job.get('jobtitle', 'N/A')
        city = job.get('city', 'N/A')
        state = job.get('stateprovince', '')
        is_remote = job.get('remote', '').lower() == 'yes'
        job_id = job.get('unique_job_number', 'N/A')

        is_new = job_id in new_job_ids # Check if job is new
        new_indicator_html = '<span class="new-tag">NEW</span> ' if is_new else ''
        tr_class = ' class="new-job"' if is_new else ''
        desc_row_class = 'description-row new-job' if is_new else 'description-row' # Class for description row

        # Format the posted date in CST
        posted_date_str = 'N/A'
        if date_posted := job.get('date_posted'):
            try:
                posted_dt = datetime.fromisoformat(date_posted.replace('Z', '+00:00'))
                posted_dt_cst = posted_dt.astimezone(cst)  # cst timezone object is already defined above
                posted_date_str = posted_dt_cst.strftime('%Y-%m-%d %H:%M %Z')
            except (ValueError, AttributeError):
                posted_date_str = date_posted  # Fallback to raw value if parsing fails

        job_url = job.get('job_detail_url', '#')

        location_str = f"{city}, {state}" if not is_remote else "Remote (US)"

        pay_min_str = job.get('payrate_min')
        pay_max_str = job.get('payrate_max')
        pay_period = job.get('payrate_period', '').lower()
        pay_rate_str = "N/A"
        if pay_min_str and pay_max_str and pay_period:
             try: # Handle potential float conversion errors
                pay_min = int(float(pay_min_str))
                pay_max = int(float(pay_max_str))
                pay_rate_str = f"${pay_min:,} - ${pay_max:,}/{pay_period}"
             except (ValueError, TypeError):
                 pay_rate_str = f"{pay_min_str} - {pay_max_str} ({pay_period})" # Fallback

        # Main job data row
        html_content += f"""
            <tr{tr_class}>
                <td>{new_indicator_html}<a href="{job_url}" target="_blank">{title}</a></td>
                <td class="location">{location_str}</td>
                <td class="pay-rate">{pay_rate_str}</td>
                <td>{job_id}</td>
                <td>{posted_date_str}</td>
            </tr>"""

        # Separate row for the description details
        html_content += f"""
            <tr class="{desc_row_class}">
                <td colspan="5">
                    <details>
                        <summary>View Job Details</summary>
                        <div class="job-description">
                            {job.get('description', 'No description available.')}
                        </div>
                    </details>
                </td>
            </tr>
"""

    html_content += """
        </tbody>
    </table>
</body>
</html>
"""
    return html_content


def _find_latest_json_report(output_dir: Path, filename_prefix: str, state_filter: str) -> Path | None:
    """Finds the most recent JSON report file in the output directory."""
    try:
        # Generate the expected filename pattern
        pattern = f"{filename_prefix}_{state_filter.lower()}_jobs_*.json"
        files = list(output_dir.glob(pattern))

        if not files:
            logger.info(f"No previous report files found matching pattern '{pattern}' in {output_dir}")
            return None

        # Sort files based on the timestamp in the filename (descending)
        # Example filename: roberthalf_tx_jobs_20250409_015900.json
        files.sort(key=lambda f: f.name.split('_')[-1].split('.')[0], reverse=True)

        latest_file = files[0]
        logger.info(f"Found latest previous report file: {latest_file.name}")
        return latest_file

    except Exception as e:
        logger.error(f"Error finding latest JSON report in {output_dir}: {e}")
        return None

def _load_job_ids_from_json(json_file_path: Path) -> set[str]:
    """Loads job IDs from a specified JSON report file."""
    job_ids: set[str] = set()
    if not json_file_path or not json_file_path.exists():
        return job_ids # Return empty set if no file

    try:
        with open(json_file_path, encoding='utf-8') as f:
            data = json.load(f)
        jobs = data.get("jobs", [])
        for job in jobs:
            job_id = job.get("unique_job_number")
            if job_id:
                job_ids.add(job_id)
        logger.info(f"Loaded {len(job_ids)} job IDs from previous report: {json_file_path.name}")
    except (FileNotFoundError, json.JSONDecodeError, Exception) as e:
        logger.warning(f"Could not load or parse previous report {json_file_path.name}: {e}. Treating as no previous jobs.")

    return job_ids


# (Keep _run_git_command and _commit_and_push_report as they are)
def _run_git_command(command: list[str], cwd: Path, sensitive: bool = False) -> tuple[bool, str, str]:
    """
    Runs a Git command using subprocess, logs carefully, and returns success, stdout, stderr.
    :param command: List of command arguments.
    :param cwd: Working directory.
    :param sensitive: If True, prevents command args from being logged (for commands with tokens).
    :return: Tuple (success_boolean, stdout_string, stderr_string)
    """
    cmd_display = " ".join(command) if not sensitive else f"{command[0]} {command[1]} [args hidden]"
    try:
        logger.info(f"Running command: {cmd_display} in {cwd}")
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False, # Don't automatically raise on non-zero exit, check manually
            encoding='utf-8',
            errors='replace' # Handle potential encoding errors in output
        )
        stdout = result.stdout.strip() if result.stdout else ""
        stderr = result.stderr.strip() if result.stderr else ""

        if result.returncode == 0:
            logger.info(f"Git command successful. stdout:\n{stdout}" if stdout else "Git command successful.")
            if stderr:
                 logger.warning(f"Git command stderr:\n{stderr}")
            return True, stdout, stderr
        else:
            # Log sensitive command details carefully on error
            logger.error(f"Git command failed: {cmd_display}")
            logger.error(f"Return code: {result.returncode}")
            logger.error(f"Stdout:\n{stdout}")
            logger.error(f"Stderr:\n{stderr}")
            return False, stdout, stderr

    except FileNotFoundError:
        logger.error(f"Git command failed: '{command[0]}' executable not found. Ensure Git is installed and in PATH.")
        return False, "", "Git executable not found"
    except Exception as e:
        logger.error(f"An unexpected error occurred running git command {cmd_display}: {e}", exc_info=True)
        return False, "", f"Unexpected error: {e}"

def _commit_and_push_report(html_file_path: Path, timestamp: str, config: dict[str, Any]) -> None:
    """Adds, commits, and pushes the HTML report using Git, potentially with token auth."""
    repo_dir = Path.cwd() # Assume script runs from repo root
    commit_message = f"Update job report for {config.get('FILTER_STATE', 'N/A')} - {timestamp}"
    html_rel_path_str = str(html_file_path) # Get relative path for commands

    # Check if file exists before proceeding
    absolute_html_path = repo_dir / html_file_path
    if not absolute_html_path.exists():
        logger.error(f"HTML file {absolute_html_path} does not exist. Skipping Git operations.")
        return

    # Check Git status
    status_command = ["git", "status", "--porcelain", html_rel_path_str]
    status_ok, stdout, _ = _run_git_command(status_command, cwd=repo_dir)
    if status_ok and not stdout:
        logger.info(f"No changes detected in {html_rel_path_str}. Skipping commit and push.")
        return
    elif not status_ok:
        logger.warning(f"Could not reliably check git status for {html_rel_path_str}. Proceeding with add/commit/push attempt.")
    else:
        logger.info(f"Changes detected in {html_rel_path_str}. Proceeding with Git operations.")

    # 1. Add the file
    add_ok, _, _ = _run_git_command(["git", "add", html_rel_path_str], cwd=repo_dir)
    if not add_ok:
        logger.error(f"Failed to git add {html_rel_path_str}. Aborting push.")
        return

    # 2. Commit the changes
    commit_ok, _, _ = _run_git_command(["git", "commit", "-m", commit_message], cwd=repo_dir)
    if not commit_ok:
        logger.error("Failed to git commit. Aborting push.")
        # Attempt to reset head if commit failed but add succeeded
        _run_git_command(["git", "reset", "HEAD", html_rel_path_str], cwd=repo_dir)
        return

    # 3. Push the changes
    logger.info("Attempting to push changes...")
    git_token = config.get('GITHUB_ACCESS_TOKEN')
    push_command = ["git", "push"]
    sensitive_push = False

    # Try to use token if provided
    if git_token:
        logger.debug("GitHub token found, attempting to construct authenticated push URL.")
        # Get remote push URL
        remote_url_ok, remote_url, remote_err = _run_git_command(["git", "remote", "get-url", "--push", "origin"], cwd=repo_dir)

        if remote_url_ok and remote_url:
            try:
                parsed_url = urlparse(remote_url)
                if parsed_url.scheme == 'https' and parsed_url.hostname:
                    # Get current branch name
                    branch_ok, current_branch, branch_err = _run_git_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir)
                    if branch_ok and current_branch:
                        # Construct authenticated URL
                        host = parsed_url.hostname
                        path = parsed_url.path
                        if path.startswith('/'):
                            path = path[1:] # Remove leading slash if present
                        authenticated_url = f"https://{git_token}@{host}/{path}"
                        logger.info(f"Using token authentication to push to {parsed_url.scheme}://[hidden]@{host}/{path}")

                        # Update push command
                        push_command = ["git", "push", authenticated_url, current_branch]
                        sensitive_push = True # Mark command as sensitive to hide token in logs
                    else:
                        logger.warning(f"Could not determine current branch: {branch_err}. Falling back to default push.")
                else:
                    logger.warning(f"Remote 'origin' URL is not HTTPS ({remote_url}). Token cannot be used. Falling back to default push (e.g., SSH key).")
            except Exception as e:
                 logger.warning(f"Error parsing remote URL or constructing authenticated URL: {e}. Falling back to default push.")
        else:
             logger.warning(f"Could not get remote push URL for 'origin': {remote_err}. Falling back to default push.")
    else:
        logger.info("No GitHub token provided. Using default Git push command (relies on ambient auth like SSH keys or credential helper).")

    # Execute the push command (either default or with authenticated URL)
    push_ok, _, push_err = _run_git_command(push_command, cwd=repo_dir, sensitive=sensitive_push)

    if not push_ok:
        logger.error(f"Failed to git push. Error: {push_err}")
        # Note: Consider resetting the commit if push fails?
        # For now, just log the error. The commit remains local.
    else:
        logger.info("Successfully pushed updated job report.")


def save_job_results(jobs_list: list[dict[str, Any]], total_found: int, config: dict[str, Any], filename_prefix: str = "roberthalf") -> None:
    """Save the final list of jobs to JSON and generate/commit/push an HTML report."""
    # Define paths using constants
    output_dir = OUTPUT_DIR
    docs_dir = DOCS_DIR
    timestamp_dt = datetime.now(UTC) # Use UTC consistent with session logic
    timestamp_str = timestamp_dt.strftime("%Y%m%d_%H%M%S")
    iso_timestamp_str = timestamp_dt.isoformat().replace('+00:00', 'Z') # For HTML report

    state_filter = config.get('FILTER_STATE', 'N/A') # Get from config
    job_period = config.get('JOB_POST_PERIOD', 'N/A') # Get from config
    test_mode = config.get('TEST_MODE', False) # Get from config
    pushover_enabled = config.get('PUSHOVER_ENABLED', False) # Get from config
    github_pages_url = config.get('GITHUB_PAGES_URL') # Get from config

    # Count TX and remote jobs
    tx_jobs = [job for job in jobs_list if job.get('stateprovince') == state_filter]
    remote_jobs = [job for job in jobs_list if job.get('remote', '').lower() == 'yes']

    # --- Determine New Jobs ---
    previous_report_file = _find_latest_json_report(output_dir, filename_prefix, state_filter)
    previous_job_ids = _load_job_ids_from_json(previous_report_file)
    current_job_ids = {job.get("unique_job_number") for job in jobs_list if job.get("unique_job_number")}
    new_job_ids = current_job_ids - previous_job_ids
    logger.info(f"Identified {len(new_job_ids)} new jobs compared to the previous report.")
    # --- End Determine New Jobs ---


    # Ensure output and docs directories exist
    for dir_path in [output_dir, docs_dir]:
        try:
            dir_path.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Ensured directory exists: {dir_path.resolve()}")
        except OSError as e:
            logger.error(f"Failed to create directory {dir_path}: {e}")
            return # Stop if directories can't be created

    # --- Save JSON Results ---
    json_filename = f"{filename_prefix}_{state_filter.lower()}_jobs_{timestamp_str}.json"
    json_output_file_path = output_dir / json_filename

    # Add 'is_new' flag to job data before saving JSON (optional, but could be useful)
    for job in jobs_list:
        job['is_new'] = job.get('unique_job_number') in new_job_ids

    results_data = {
        "jobs": jobs_list, # Now includes 'is_new' flag
        "timestamp": iso_timestamp_str, # Use ISO format timestamp
        f"total_{state_filter.lower()}_jobs": len(tx_jobs),
        "total_remote_jobs": len(remote_jobs),
        "total_new_jobs": len(new_job_ids), # Add count of new jobs
        "total_jobs_found_in_period": total_found,
        "job_post_period_filter": job_period,
        "state_filter": state_filter,
        "status": "Completed"
    }

    try:
        with open(json_output_file_path, 'w', encoding='utf-8') as f:
            json.dump(results_data, f, indent=2, ensure_ascii=False)
        logger.info(f"Saved {len(jobs_list)} jobs ({len(tx_jobs)} in {state_filter}, {len(remote_jobs)} remote, {len(new_job_ids)} new) to {json_output_file_path.resolve()}")
    except Exception as e:
        logger.error(f"Failed to save JSON job results to {json_output_file_path.resolve()}: {e}")

    # --- Generate and Save HTML Report ---
    html_filename = "jobs.html" # Fixed filename for GitHub pages
    html_output_file_path = docs_dir / html_filename # Ensure it's relative for Git commands

    try:
        # Pass the set of new job IDs to the generator
        html_content = _generate_html_report(
            jobs_list, iso_timestamp_str, total_found, state_filter, job_period, new_job_ids
        )
        with open(html_output_file_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        logger.info(f"Generated HTML report at: {html_output_file_path.resolve()}")

        # --- Commit and Push HTML Report ---
        # Pass the config dictionary here
        if len(jobs_list) > 0 or test_mode: # Only commit/push if new jobs found or testing
             _commit_and_push_report(html_output_file_path, timestamp_str, config) # Pass config
        else:
             logger.info("No new jobs found and not in test mode. Skipping Git commit/push.")

    except Exception as e:
        logger.error(f"Failed to generate, save, or commit/push HTML report to {html_output_file_path.resolve()}: {e}", exc_info=True)

    # --- Send Notification ---
    if pushover_enabled and (len(jobs_list) > 0 or test_mode):
        # Format job details for notification
        job_details = []
        # Sort for notification consistency (same key as HTML)
        jobs_list.sort(key=lambda x: (x.get('date_posted', '1970-01-01T00:00:00Z'), x.get('jobtitle', '')), reverse=True) # Ensure valid default date

        # Prepare list of top jobs, including new ones first if possible
        new_jobs_top = [j for j in jobs_list if j.get('unique_job_number') in new_job_ids][:5]
        old_jobs_top = [j for j in jobs_list if j.get('unique_job_number') not in new_job_ids][:5 - len(new_jobs_top)]
        top_jobs_for_notification = new_jobs_top + old_jobs_top

        for job in top_jobs_for_notification: # Show up to 5 jobs
            job_id = job.get('unique_job_number')
            is_new = job_id in new_job_ids
            # Use HTML for Pushover notification styling
            new_indicator_pushover = '<b><font color="#28a745">NEW!</font></b> ' if is_new else ''

            title = job.get('jobtitle', 'Unknown Title')
            city = job.get('city', 'Unknown City')
            state = job.get('stateprovince', '')
            is_remote = job.get('remote', '').lower() == 'yes'
            pay_min_str = job.get('payrate_min')
            pay_max_str = job.get('payrate_max')
            pay_period = job.get('payrate_period', '').lower()

            location = "Remote" if is_remote else f"{city}, {state}"
            # Add the new indicator before the bullet point
            detail = f"{new_indicator_pushover}• {title} ({location})"
            if pay_min_str and pay_max_str and pay_period:
                with contextlib.suppress(ValueError, TypeError): # Omit pay if formatting pay fails
                    detail += f"\n  ${int(float(pay_min_str)):,} - ${int(float(pay_max_str)):,}/{pay_period}"
            job_details.append(detail)

        # Use single backslash for newline join
        details_text = '\n'.join(job_details)
        remaining = len(jobs_list) - len(top_jobs_for_notification)

        # --- Construct notification message ---
        # Use count of new jobs from the calculated set
        num_new_jobs_found = len(new_job_ids)
        num_tx_jobs_state = len(tx_jobs) # Total state jobs in current report
        num_remote_jobs_state = len(remote_jobs) # Total remote jobs in current report

        if test_mode and len(jobs_list) == 0:
             # Test mode with no actual jobs found
             message = (
                    f"🧪 TEST MODE: Simulating job notification!\n\n"
                    f"<b><font color=\"#28a745\">NEW!</font></b> Found 3 test jobs in {state_filter}:\n"
                    f"<b><font color=\"#28a745\">NEW!</font></b> • Test Software Engineer (Austin)\n  $120,000 - $150,000/yearly\n"
                    f"<b><font color=\"#28a745\">NEW!</font></b> • Test Developer (Dallas)\n  $130,000 - $160,000/yearly\n"
                    f"• Test DevOps Engineer (Houston)\n  $140,000 - $170,000/yearly" # Example mixed
                    f"\n\nClick link to view simulated HTML report."
             )
        else:
            # Regular mode or test mode with actual jobs
            if num_new_jobs_found > 0:
                 message = f"Found {num_new_jobs_found} NEW jobs! ({num_tx_jobs_state} in {state_filter}, {num_remote_jobs_state} remote total) in the {job_period.lower().replace('_', ' ')}."
            else:
                 message = f"No new jobs found. ({num_tx_jobs_state} in {state_filter}, {num_remote_jobs_state} remote total) in the {job_period.lower().replace('_', ' ')}."

            if job_details:
                # Use single backslash for newlines
                message += f"\n\nLatest/Newest:\n{details_text}"
            if remaining > 0:
                # Use single backslash for newlines
                message += f"\n\n...and {remaining} more jobs"
            # Use single backslash for newlines
            message += "\n\nClick the link below to view the full list." # Updated call to action

        try:
            # Validate the GitHub Pages URL from config
            pushover_url = None
            pushover_url_title = None
            if not github_pages_url:
                logger.warning("GITHUB_PAGES_URL not set in config. Pushover notification will not have a specific report URL.")
            elif "YOUR_USERNAME" in github_pages_url or "YOUR_REPO_NAME" in github_pages_url:
                logger.warning("GITHUB_PAGES_URL seems to contain placeholders. Pushover notification URL might be incorrect.")
                pushover_url = github_pages_url # Send potentially incorrect URL but warn
                pushover_url_title = f"View Full {state_filter}/Remote Job List"
            else:
                # URL looks okay
                pushover_url = github_pages_url
                pushover_url_title = f"View Full {state_filter}/Remote Job List"

            # Send notification (pushnotify.py reads env vars directly for tokens/keys)
            # ADD html=1 parameter
            send_pushover_notification(
                message=message,
                user="Joe", # Consider making user configurable via .env if needed
                title=f"Robert Half {state_filter} & Remote Jobs",
                url=pushover_url, # Use validated/logged URL
                url_title=pushover_url_title, # Use associated title
                html=1 # Enable HTML formatting for the message
            )
            logger.info("Push notification sent successfully with HTML enabled.")
        except Exception as notify_err:
            logger.error(f"Failed to send push notification: {notify_err}")
    elif not pushover_enabled:
         logger.info("Pushover notifications are disabled via PUSHOVER_ENABLED=false.")


def scrape_roberthalf_jobs() -> None:
    """Main function to orchestrate the Robert Half job scraping."""
    logger.info("--- Starting Robert Half Job Scraper ---")
    start_time = time.time()

    # Access the globally loaded config dictionary
    global config
    if not config:
         logger.critical("Configuration was not loaded successfully. Exiting.")
         return

    try:
        # Get or refresh session (cookies + user_agent)
        session_cookies, session_user_agent = get_or_refresh_session() # Uses new session functions

        all_filtered_jobs = []
        total_jobs_api_reported = 0 # Renamed for clarity

        # Fetch both local and remote jobs
        for is_remote in [False, True]:
            page_number = 1
            jobs_found_this_type = None # Track count for this type (local/remote)

            while True:
                job_type_str = 'Remote' if is_remote else 'Local'
                logger.info(f"--- Processing {job_type_str} Page {page_number} ---")

                # Fetch data for the current page with retries
                response_data = fetch_with_retry(session_cookies, session_user_agent, page_number, is_remote)

                if not response_data:
                    logger.warning(f"Fetch failed for {job_type_str} page {page_number}. Validating session.")
                    is_valid = validate_session(session_cookies, session_user_agent)
                    if not is_valid:
                        logger.error("Session became invalid during pagination. Stopping scrape.")
                        raise RuntimeError("Session became invalid and could not be refreshed during pagination.")
                    else:
                        logger.error(f"Session appears valid, but failed to fetch {job_type_str} page {page_number} after retries. Stopping.")
                        raise RuntimeError(f"Failed to fetch {job_type_str} page {page_number} despite valid session.")


                # Extract total count *for this type* only once
                if jobs_found_this_type is None:
                    try:
                        current_found = int(response_data.get('found', 0))
                        jobs_found_this_type = current_found # Store count for this type
                        total_jobs_api_reported += current_found # Add to overall total
                        logger.info(f"API reports {current_found} total {job_type_str} jobs found for period '{JOB_POST_PERIOD}'")
                    except (ValueError, TypeError):
                        logger.warning("Could not parse 'found' count from API response.")
                        jobs_found_this_type = -1 # Indicate parsing failed

                jobs_on_page = response_data.get('jobs', [])
                if not jobs_on_page:
                    logger.info(f"No more {job_type_str} jobs found on page {page_number}. Reached the end for this type.")
                    break

                logger.info(f"Received {len(jobs_on_page)} {job_type_str} jobs on page {page_number}.")

                # Filter jobs by state (or remote)
                state_jobs_on_page = filter_jobs_by_state(jobs_on_page, FILTER_STATE)
                all_filtered_jobs.extend(state_jobs_on_page)

                # Check if this was the last page based on API reporting fewer than page size
                if len(jobs_on_page) < 25:
                    logger.info(f"Received less than page size ({len(jobs_on_page)} < 25). Assuming last page for {job_type_str} jobs.")
                    break

                # Check if we've fetched more pages than reasonably expected based on 'found' count
                if jobs_found_this_type is not None and jobs_found_this_type >= 0:
                     max_pages_expected = (jobs_found_this_type + 24) // 25 # Ceiling division
                     if page_number >= max_pages_expected:
                         logger.info(f"Reached expected maximum page number ({page_number}/{max_pages_expected}) based on API 'found' count. Stopping {job_type_str} pagination.")
                         break

                page_number += 1
                # Add delays between requests
                page_delay = random.uniform(PAGE_DELAY_MIN, PAGE_DELAY_MAX)
                logger.info(f"Waiting {page_delay:.2f} seconds before fetching {job_type_str} page {page_number}")
                time.sleep(page_delay)

            # Add delay between remote and local job fetching
            if not is_remote:
                switch_delay = random.uniform(PAGE_DELAY_MIN * 1.5, PAGE_DELAY_MAX * 1.5) # Slightly adjusted delay
                logger.info(f"Finished local jobs. Switching to remote jobs. Waiting {switch_delay:.2f} seconds...")
                time.sleep(switch_delay)

        # Remove duplicates after fetching both types
        unique_jobs_dict = {}
        duplicates_found = 0
        for job in all_filtered_jobs:
            job_id = job.get('unique_job_number')
            if job_id:
                if job_id not in unique_jobs_dict:
                    unique_jobs_dict[job_id] = job
                else:
                    duplicates_found += 1
            else:
                 logger.warning(f"Job found without a 'unique_job_number': {job.get('jobtitle', 'N/A')}")

        unique_job_list = list(unique_jobs_dict.values())
        if duplicates_found > 0:
             logger.info(f"Removed {duplicates_found} duplicate job entries. Final unique count: {len(unique_job_list)}")
        else:
             logger.info(f"Found {len(unique_job_list)} unique jobs (no duplicates detected).")

        # Save final results, passing the total reported by the API and the config
        save_job_results(unique_job_list, total_jobs_api_reported, config) # Pass config here

    except RuntimeError as rt_err:
        logger.critical(f"Runtime error, likely session or fetch failure: {rt_err}")
    except ValueError as val_err:
        logger.critical(f"Configuration error: {val_err}")
    except Exception as e:
        logger.critical(f"An unexpected critical error occurred in the main process: {e}", exc_info=True)
    finally:
        end_time = time.time()
        logger.info("--- Robert Half Job Scraper Finished ---")
        logger.info(f"Total execution time: {end_time - start_time:.2f} seconds")


if __name__ == "__main__":
    scrape_roberthalf_jobs()
