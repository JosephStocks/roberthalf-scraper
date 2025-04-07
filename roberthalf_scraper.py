import json
import logging
import os
import random
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

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
        saved_timestamp = datetime.fromisoformat(saved_timestamp_str)
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
    logger.info(f"Before filtering - received {len(jobs)} jobs:")
    for job in jobs:
        logger.info(f"Job ID: {job.get('unique_job_number', 'N/A')}, "
                   f"Title: {job.get('jobtitle', 'N/A')}, "
                   f"State: {job.get('stateprovince', 'N/A')}, "
                   f"Remote: {job.get('remote', 'N/A')}, "
                   f"Country: {job.get('country', 'N/A')}")

    filtered_jobs = [
        job for job in jobs
        if (job.get('stateprovince') == state_code) or  # Jobs in Texas
        (job.get('remote', '').lower() == 'yes' and job.get('country', '').lower() == 'us')  # US remote jobs
    ]

    # Count types of jobs for logging
    state_jobs = len([j for j in filtered_jobs if j.get('stateprovince') == state_code])
    remote_jobs = len([j for j in filtered_jobs if j.get('remote', '').lower() == 'yes'])

    # Debug log filtered jobs
    logger.info(f"After filtering - kept {len(filtered_jobs)} jobs:")
    for job in filtered_jobs:
        logger.info(f"Job ID: {job.get('unique_job_number', 'N/A')}, "
                   f"Title: {job.get('jobtitle', 'N/A')}, "
                   f"State: {job.get('stateprovince', 'N/A')}, "
                   f"Remote: {job.get('remote', 'N/A')}, "
                   f"Country: {job.get('country', 'N/A')}")

    logger.info(f"Filtered {state_jobs} {state_code} jobs and {remote_jobs} remote jobs from {len(jobs)} total jobs on page")
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
        "remote": "yes" if is_remote else "No",
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

def _generate_html_report(jobs_list: list[dict[str, Any]], timestamp: str, total_found: int, state_filter: str, job_period: str) -> str:
    """Generates an HTML report string from the job list."""
    num_tx_jobs = len([job for job in jobs_list if job.get('stateprovince') == state_filter])
    num_remote_jobs = len([job for job in jobs_list if job.get('remote', '').lower() == 'yes'])

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
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #f2f2f2; }}
        tr:nth-child(even) {{ background-color: #f9f9f9; }}
        a {{ color: #007bff; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        .pay-rate {{ white-space: nowrap; }}
        .location {{ white-space: nowrap; }}
    </style>
</head>
<body>
    <h1>Robert Half Job Report</h1>
    <p>Generated: {datetime.fromisoformat(timestamp.replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M:%S %Z')}</p>
    <p>Filters: State = {state_filter}, Posted Within = {job_period.replace('_', ' ')}</p>
    <p>Found {num_tx_jobs} jobs in {state_filter} and {num_remote_jobs} remote jobs (Total Unique: {len(jobs_list)}). API reported {total_found} total jobs matching period.</p>

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
    jobs_list.sort(key=lambda x: (x.get('original_published_date', '1970-01-01'), x.get('jobtitle', '')), reverse=True)

    for job in jobs_list:
        title = job.get('jobtitle', 'N/A')
        city = job.get('city', 'N/A')
        state = job.get('stateprovince', '')
        is_remote = job.get('remote', '').lower() == 'yes'
        job_id = job.get('unique_job_number', 'N/A')
        posted_date_str = job.get('original_published_date', 'N/A') # Assuming 'original_published_date' field exists
        job_url = job.get('job_detail_url', '#') # Use the correct key 'job_detail_url'

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

        html_content += f"""
            <tr>
                <td><a href="{job_url}" target="_blank">{title}</a></td>
                <td class="location">{location_str}</td>
                <td class="pay-rate">{pay_rate_str}</td>
                <td>{job_id}</td>
                <td>{posted_date_str}</td>
            </tr>
"""

    html_content += """
        </tbody>
    </table>
</body>
</html>
"""
    return html_content

def _run_git_command(command: list[str], cwd: Path) -> bool:
    """Runs a Git command using subprocess and logs output/errors."""
    try:
        logger.info(f"Running command: {' '.join(command)} in {cwd}")
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=True, encoding='utf-8')
        logger.info(f"Git command stdout:\n{result.stdout}")
        if result.stderr:
             logger.warning(f"Git command stderr:\n{result.stderr}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Git command failed: {' '.join(command)}")
        logger.error(f"Return code: {e.returncode}")
        logger.error(f"Stdout:\n{e.stdout}")
        logger.error(f"Stderr:\n{e.stderr}")
        return False
    except FileNotFoundError:
        logger.error("Git command failed: 'git' executable not found. Ensure Git is installed and in PATH.")
        return False
    except Exception as e:
        logger.error(f"An unexpected error occurred running git command: {e}")
        return False

def _commit_and_push_report(html_file_path: Path, timestamp: str) -> None:
    """Adds, commits, and pushes the HTML report using Git."""
    repo_dir = Path.cwd() # Assume script runs from repo root
    commit_message = f"Update job report for {FILTER_STATE} - {timestamp}"

    # html_file_path IS ALREADY the relative path (e.g., Path("docs/jobs.html"))
    # We just need its string representation for subprocess
    html_rel_path_str = str(html_file_path)

    # Check if there are changes to commit first
    status_command = ["git", "status", "--porcelain", html_rel_path_str]
    try:
        # Make sure the path exists before checking status
        absolute_html_path = repo_dir / html_file_path
        if not absolute_html_path.exists():
            logger.error(f"HTML file {absolute_html_path} does not exist. Skipping Git operations.")
            return

        result = subprocess.run(status_command, cwd=repo_dir, capture_output=True, text=True, check=True, encoding='utf-8')
        if not result.stdout.strip():
            logger.info(f"No changes detected in {html_rel_path_str}. Skipping commit and push.")
            return
        else:
             logger.info(f"Changes detected in {html_rel_path_str}:\n{result.stdout.strip()}")
    except FileNotFoundError:
         # This might happen if the file was unexpectedly deleted between creation and here
         logger.error(f"HTML file {absolute_html_path} not found when checking git status.")
         return
    except subprocess.CalledProcessError as e:
         # Handle case where git status fails (e.g., not a git repo, file not tracked initially?)
         logger.warning(f"Git status check failed (maybe file isn't tracked yet?): {e}. Proceeding with add/commit attempt.")
    except Exception as e:
        logger.warning(f"Could not check git status reliably: {e}. Proceeding with add/commit/push attempt.")


    # 1. Add the file
    if not _run_git_command(["git", "add", html_rel_path_str], cwd=repo_dir):
        logger.error(f"Failed to git add {html_rel_path_str}. Aborting push.")
        return

    # 2. Commit the changes
    if not _run_git_command(["git", "commit", "-m", commit_message], cwd=repo_dir):
        logger.error("Failed to git commit. Aborting push.")
        # Attempt to reset head if commit failed but add succeeded
        _run_git_command(["git", "reset", "HEAD", html_rel_path_str], cwd=repo_dir)
        return

    # 3. Push the changes
    if not _run_git_command(["git", "push"], cwd=repo_dir):
        logger.error("Failed to git push.")
        # Note: Consider more complex error handling like git reset --hard HEAD~1 if push fails?
        # For now, just log the error. The commit is local.
    else:
        logger.info("Successfully pushed updated job report.")


def save_job_results(jobs_list: list[dict[str, Any]], total_found: int, filename_prefix: str = "roberthalf") -> None:
    """Save the final list of jobs to JSON and generate/commit/push an HTML report."""
    # Define paths using constants
    output_dir = OUTPUT_DIR
    docs_dir = DOCS_DIR
    timestamp_dt = datetime.now(UTC) # Use UTC consistent with session logic
    timestamp_str = timestamp_dt.strftime("%Y%m%d_%H%M%S")
    iso_timestamp_str = timestamp_dt.isoformat().replace('+00:00', 'Z') # For HTML report


    # Count TX and remote jobs
    tx_jobs = [job for job in jobs_list if job.get('stateprovince') == FILTER_STATE]
    remote_jobs = [job for job in jobs_list if job.get('remote', '').lower() == 'yes']

    # Ensure output and docs directories exist
    for dir_path in [output_dir, docs_dir]:
        try:
            dir_path.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Ensured directory exists: {dir_path.resolve()}")
        except OSError as e:
            logger.error(f"Failed to create directory {dir_path}: {e}")
            # Optionally decide whether to proceed for critical dirs like docs

    # --- Save JSON Results (Existing Logic) ---
    json_filename = f"{filename_prefix}_{FILTER_STATE.lower()}_jobs_{timestamp_str}.json"
    json_output_file_path = output_dir / json_filename

    results_data = {
        "jobs": jobs_list,
        "timestamp": iso_timestamp_str, # Use ISO format timestamp
        f"total_{FILTER_STATE.lower()}_jobs": len(tx_jobs),
        "total_remote_jobs": len(remote_jobs),
        "total_jobs_found_in_period": total_found,
        "job_post_period_filter": JOB_POST_PERIOD,
        "state_filter": FILTER_STATE,
        "status": "Completed"
    }

    try:
        with open(json_output_file_path, 'w', encoding='utf-8') as f:
            json.dump(results_data, f, indent=2, ensure_ascii=False)
        logger.info(f"Saved {len(jobs_list)} jobs ({len(tx_jobs)} in {FILTER_STATE}, {len(remote_jobs)} remote) to {json_output_file_path.resolve()}")
    except Exception as e:
        logger.error(f"Failed to save JSON job results to {json_output_file_path.resolve()}: {e}")

    # --- Generate and Save HTML Report ---
    html_filename = "jobs.html"
    html_output_file_path = docs_dir / html_filename

    try:
        html_content = _generate_html_report(jobs_list, iso_timestamp_str, total_found, FILTER_STATE, JOB_POST_PERIOD)
        with open(html_output_file_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        logger.info(f"Generated HTML report at: {html_output_file_path.resolve()}")

        # --- Commit and Push HTML Report ---
        if len(jobs_list) > 0 or TEST_MODE: # Only commit/push if new jobs found or testing
             _commit_and_push_report(html_output_file_path, timestamp_str)
        else:
             logger.info("No new jobs found and not in test mode. Skipping Git commit/push.")

    except Exception as e:
        logger.error(f"Failed to generate or save HTML report to {html_output_file_path.resolve()}: {e}")

    # --- Send Notification ---
    if len(jobs_list) > 0 or TEST_MODE:
        # Format job details for notification (same as before)
        job_details = []
        # Sort for notification consistency (same key as HTML)
        jobs_list.sort(key=lambda x: (x.get('original_published_date', '1970-01-01'), x.get('jobtitle', '')), reverse=True)
        for job in jobs_list[:5]:
            # ... (rest of job detail formatting logic remains unchanged) ...
            title = job.get('jobtitle', 'Unknown Title')
            city = job.get('city', 'Unknown City')
            state = job.get('stateprovince', '')
            is_remote = job.get('remote', '').lower() == 'yes'
            pay_min = job.get('payrate_min')
            pay_max = job.get('payrate_max')
            pay_period = job.get('payrate_period', '').lower()

            location = "Remote" if is_remote else f"{city}, {state}"
            detail = f"• {title} ({location})"
            if pay_min and pay_max and pay_period:
                 try:
                     # Use single backslash for newline
                     detail += f"\n  ${int(float(pay_min)):,} - ${int(float(pay_max)):,}/{pay_period}"
                 except (ValueError, TypeError):
                      pass # Ignore if pay can't be formatted nicely for notification
            job_details.append(detail)

        # Use single backslash for newline join
        details_text = '\n'.join(job_details)
        remaining = len(jobs_list) - 5 if len(jobs_list) > 5 else 0

        # Construct notification message (same as before, but adjust URL prompt)
        if TEST_MODE and len(jobs_list) == 0:
             message = (
                 # Use single backslash for newlines in test message
                    f"🧪 TEST MODE: Simulating job notification!\n\n"
                    f"Found 3 test jobs in {FILTER_STATE}:\n"
                    f"• Test Software Engineer (Austin)\n  $120,000 - $150,000/yearly\n"
                    f"• Test Developer (Dallas)\n  $130,000 - $160,000/yearly\n"
                    f"• Test DevOps Engineer (Houston)\n  $140,000 - $170,000/yearly"
                    f"\n\nClick link to view simulated HTML report." # Adjusted test message
             )
        else:
            message = f"Found {len(tx_jobs)} new {FILTER_STATE} jobs and {len(remote_jobs)} remote jobs in the {JOB_POST_PERIOD.lower().replace('_', ' ')}!"
            if job_details:
                # Use single backslash for newlines
                message += f"\n\nLatest positions:\n{details_text}"
            if remaining > 0:
                # Use single backslash for newlines
                message += f"\n\n...and {remaining} more jobs"
            # Update the call to action
            # Use single backslash for newlines
            message += "\n\nClick the link below to view the full list." # Updated call to action

        try:
            github_pages_url = "https://JosephStocks.github.io/roberthalf-scraper/jobs.html"
            # Check if placeholder is still set and warn if so
            if "YOUR_USERNAME" in github_pages_url or "YOUR_REPO_NAME" in github_pages_url:
                 logger.warning("Pushover URL still contains placeholders! Please update 'github_pages_url' in save_job_results.")
                 # Optionally, don't send notification or use a fallback URL if placeholders are detected
                 # return

            send_pushover_notification(
                message=message,
                user="Joe", # Consider making user configurable if needed
                title=f"Robert Half {FILTER_STATE} & Remote Jobs", # Slightly updated title
                url=github_pages_url, # Use the GitHub Pages URL
                url_title=f"View Full {FILTER_STATE}/Remote Job List" # Updated URL title
            )
            logger.info("Push notification sent successfully pointing to HTML report.")
        except Exception as notify_err:
            logger.error(f"Failed to send push notification: {notify_err}")

def scrape_roberthalf_jobs() -> None:
    """Main function to orchestrate the Robert Half job scraping."""
    logger.info("--- Starting Robert Half Job Scraper ---")
    start_time = time.time()

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
                logger.info(f"--- Processing {'Remote' if is_remote else 'Local'} Page {page_number} ---")

                # Fetch data for the current page with retries
                response_data = fetch_with_retry(session_cookies, session_user_agent, page_number, is_remote)

                if not response_data:
                    # Check if the session might be invalid
                    logger.warning(f"Fetch failed for {'remote' if is_remote else 'local'} page {page_number}. Trying to validate session.")
                    is_valid = validate_session(session_cookies, session_user_agent)
                    if not is_valid:
                        logger.error("Session became invalid during pagination. Stopping.")
                        break
                    else:
                        logger.error(f"Session still seems valid, but failed to fetch page {page_number} after retries. Stopping.")
                        break

                # Extract total count *for this type* only once
                if jobs_found_this_type is None:
                    try:
                        current_found = int(response_data.get('found', 0))
                        jobs_found_this_type = current_found # Store count for this type
                        total_jobs_api_reported += current_found # Add to overall total
                        logger.info(f"API reports {current_found} total {'remote' if is_remote else 'local'} jobs found for period '{JOB_POST_PERIOD}'")
                    except (ValueError, TypeError):
                        logger.warning("Could not parse 'found' count from API response.")
                        jobs_found_this_type = -1 # Indicate parsing failed

                jobs_on_page = response_data.get('jobs', [])
                if not jobs_on_page:
                    logger.info(f"No more {'remote' if is_remote else 'local'} jobs found on page {page_number}. Reached the end.")
                    break

                logger.info(f"Received {len(jobs_on_page)} {'remote' if is_remote else 'local'} jobs on page {page_number}.")

                # Filter jobs by state (or remote)
                state_jobs_on_page = filter_jobs_by_state(jobs_on_page, FILTER_STATE)
                all_filtered_jobs.extend(state_jobs_on_page)

                # Check if this was the last page
                if len(jobs_on_page) < 25: # Assuming 25 is still the page size
                    logger.info(f"Received less than assumed page size ({len(jobs_on_page)} < 25). Assuming last page for {'remote' if is_remote else 'local'} jobs.")
                    break

                page_number += 1
                # Add delays between requests
                page_delay = random.uniform(PAGE_DELAY_MIN, PAGE_DELAY_MAX)
                logger.info(f"Waiting {page_delay:.2f} seconds before fetching page {page_number}")
                time.sleep(page_delay)
                time.sleep(REQUEST_DELAY_SECONDS)

            # Add delay between remote and local job fetching
            if not is_remote: # Check using 'not is_remote' for clarity (same logic as 'is_remote is False')
                switch_delay = random.uniform(PAGE_DELAY_MIN * 1.5, PAGE_DELAY_MAX * 1.5) # Slightly adjusted delay
                logger.info(f"Finished local jobs. Switching to remote jobs. Waiting {switch_delay:.2f} seconds...")
                time.sleep(switch_delay)

        # Remove duplicates after fetching both types
        unique_jobs_dict = {job.get('unique_job_number'): job for job in all_filtered_jobs if job.get('unique_job_number')}
        unique_job_list = list(unique_jobs_dict.values())
        if len(all_filtered_jobs) > len(unique_job_list):
             logger.info(f"Removed {len(all_filtered_jobs) - len(unique_job_list)} duplicate jobs. Final count: {len(unique_job_list)}")
        else:
             logger.info(f"Found {len(unique_job_list)} unique jobs (no duplicates detected).")

        # Save final results, passing the total reported by the API
        save_job_results(unique_job_list, total_jobs_api_reported) # Pass the correct total count

    except RuntimeError as rt_err:
        logger.critical(f"Runtime error, likely session failure: {rt_err}")
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
