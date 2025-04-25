import argparse
import contextlib
import csv
import json
import logging
import os
import random
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytz
import requests
from playwright.sync_api import (
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from config_loader import load_prod_config
from job_matcher_v2 import JobMatchAnalyzerV2
from pushnotify import send_pushover_notification
from utils import get_proxy_config

# --- Constants ---
LOG_DIR = Path("logs")
SESSION_DIR = Path(".session")
OUTPUT_DIR = Path("output")
DOCS_DIR = Path("docs")
CSV_FILE_PATH = OUTPUT_DIR / "job_data.csv"
DEFAULT_SESSION_FILENAME = "session_data.json"

# Create directories if they don't exist early on
LOG_DIR.mkdir(parents=True, exist_ok=True)
SESSION_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
DOCS_DIR.mkdir(parents=True, exist_ok=True)  # Ensure docs dir exists


# --- Logging Setup ---
def setup_logging(log_level_str: str = "INFO"):
    """Set up logging configuration."""
    log_file_path = LOG_DIR / "scraper.log"
    log_level = getattr(logging, log_level_str.upper(), logging.INFO)

    # Remove existing handlers if any before configuring
    root_logger = logging.getLogger()
    if root_logger.hasHandlers():
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s [%(filename)s:%(lineno)d] - %(message)s",  # Added timestamp
        handlers=[
            logging.FileHandler(log_file_path, mode="w", encoding="utf-8"),  # Use UTF-8
            logging.StreamHandler(),
        ],
    )
    # Silence overly verbose libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)  # Silence OpenAI info logs if desired
    return logging.getLogger(__name__)  # Return the specific logger for this module


# --- Load Config and Setup Logger ---
config = load_prod_config()
logger = setup_logging(config.get("LOG_LEVEL", "INFO"))  # Pass log level from config

# --- Global Config Variables (extracted after logger setup) ---
SESSION_FILE_PATH = SESSION_DIR / config.get("SESSION_FILE", DEFAULT_SESSION_FILENAME)
SAVE_SESSION = config.get("SAVE_SESSION", True)
SESSION_MAX_AGE_HOURS = config.get("SESSION_MAX_AGE_HOURS", 12)
FILTER_STATE = config.get("FILTER_STATE", "TX")
JOB_POST_PERIOD = config.get("JOB_POST_PERIOD", "PAST_24_HOURS")
HEADLESS_BROWSER = config.get("HEADLESS_BROWSER", True)
ROTATE_USER_AGENT = config.get("ROTATE_USER_AGENT", False)
DEFAULT_USER_AGENT = config.get("DEFAULT_USER_AGENT", "Mozilla/5.0 (...)")
REQUEST_DELAY_SECONDS = config.get("REQUEST_DELAY_SECONDS", 2.0)
PAGE_DELAY_MIN = config.get("PAGE_DELAY_MIN", 5.0)
PAGE_DELAY_MAX = config.get("PAGE_DELAY_MAX", 15.0)
MAX_RETRIES = config.get("MAX_RETRIES", 3)
BROWSER_TIMEOUT_MS = config.get("BROWSER_TIMEOUT_MS", 60000)
REQUEST_TIMEOUT_SECONDS = config.get("REQUEST_TIMEOUT_SECONDS", 30)
TEST_MODE = config.get("TEST_MODE", False)
GITHUB_ACCESS_TOKEN = config.get("GITHUB_ACCESS_TOKEN")
GITHUB_PAGES_URL = config.get("GITHUB_PAGES_URL")
PUSHOVER_ENABLED = config.get("PUSHOVER_ENABLED", False)


# --- Helper Functions (Keep as they are, or move utils if preferred) ---
def get_user_agent() -> str:
    # ... (implementation) ...
    if not ROTATE_USER_AGENT:
        return DEFAULT_USER_AGENT
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/133.0.2782.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
    ]
    return random.choice(user_agents)


def add_human_delay(min_seconds: float = 0.5, max_seconds: float = 1.5) -> None:
    # ... (implementation) ...
    delay = random.uniform(min_seconds, max_seconds)
    logger.debug(f"Adding browser interaction delay of {delay:.2f} seconds")
    time.sleep(delay)


def save_session_data(
    cookies: list[dict[str, Any]], user_agent: str, filename_path: Path = SESSION_FILE_PATH
) -> None:
    # ... (implementation) ...
    if not SAVE_SESSION:
        logger.info("Session saving is disabled.")
        return
    try:
        filename_path.parent.mkdir(parents=True, exist_ok=True)
        session_data = {
            "cookies": cookies,
            "user_agent": user_agent,
            "timestamp": datetime.now(UTC).isoformat(),  # Store timestamp
        }
        with open(filename_path, "w", encoding="utf-8") as f:  # Ensure UTF-8
            json.dump(session_data, f, indent=2)
        logger.info(f"Session data saved to {filename_path.resolve()}")
    except Exception as e:
        logger.error(f"Failed to save session data to {filename_path.resolve()}: {e}")


def load_session_data(
    filename_path: Path = SESSION_FILE_PATH,
) -> tuple[list[dict[str, Any]], str] | None:  # Use Optional
    # ... (implementation) ...
    if not SAVE_SESSION:
        return None
    if not filename_path.exists():
        return None
    try:
        with open(filename_path, encoding="utf-8") as f:  # Ensure UTF-8
            session_data = json.load(f)
        # ... (validation and age check logic) ...
        saved_cookies = session_data.get("cookies")
        saved_user_agent = session_data.get("user_agent")
        saved_timestamp_str = session_data.get("timestamp")

        if not saved_cookies or not saved_user_agent or not saved_timestamp_str:
            logger.warning(f"Session file {filename_path.resolve()} is incomplete. Ignoring.")
            return None

        saved_timestamp = datetime.fromisoformat(saved_timestamp_str.replace("Z", "+00:00"))
        if datetime.now(UTC) - saved_timestamp > timedelta(hours=SESSION_MAX_AGE_HOURS):
            logger.info(f"Session data in {filename_path.resolve()} has expired.")
            with contextlib.suppress(OSError):
                filename_path.unlink()
            return None

        logger.info(f"Loaded valid session data from {filename_path.resolve()}")
        return saved_cookies, saved_user_agent
    except (FileNotFoundError, json.JSONDecodeError, ValueError, TypeError, OSError) as e:
        logger.warning(f"Could not load/parse/delete session file {filename_path.resolve()}: {e}")
        with contextlib.suppress(OSError):
            filename_path.unlink()
        return None


def login_and_get_session() -> tuple[list[dict[str, Any]], str] | None:  # Use Optional
    # ... (implementation - looks mostly ok) ...
    logger.info("Starting login process with Playwright")
    session_user_agent = get_user_agent()
    logger.info(f"Using User Agent for login: {session_user_agent}")

    # Get credentials securely from global config
    username = config.get("ROBERTHALF_USERNAME")
    password = config.get("ROBERTHALF_PASSWORD")
    if not username or not password:
        logger.error("ROBERTHALF_USERNAME or ROBERTHALF_PASSWORD not found.")
        return None  # Return None on credential error

    page = None # Initialize page to None before try block
    browser = None
    context = None
    with sync_playwright() as p:
        proxy_config_dict = get_proxy_config()
        try:
            browser = p.chromium.launch(
                proxy=proxy_config_dict, headless=HEADLESS_BROWSER, timeout=BROWSER_TIMEOUT_MS
            )
            context = browser.new_context(
                proxy=proxy_config_dict,
                viewport={"width": 1920, "height": 1080},
                user_agent=session_user_agent,
                java_script_enabled=True,
                accept_downloads=False,
                ignore_https_errors=True,
            )
            context.set_default_navigation_timeout(BROWSER_TIMEOUT_MS)
            page = context.new_page()
            # ... (navigation, filling fields, clicking - add error handling) ...
            login_url = "https://online.roberthalf.com/s/login?app=0sp3w000001UJH5&c=US&d=en_US&language=en_US&redirect=false"
            logger.info(f"Navigating to login page: {login_url}")
            page.goto(login_url, wait_until="domcontentloaded", timeout=BROWSER_TIMEOUT_MS)
            add_human_delay(2, 4)

            username_field = page.locator('[data-id="username"] input')
            username_field.wait_for(state="visible", timeout=15000)
            username_field.fill(username)
            add_human_delay()

            password_field = page.locator('[data-id="password"] input')
            password_field.wait_for(state="visible", timeout=10000)
            password_field.fill(password)
            add_human_delay()

            sign_in_button = page.locator('rhcl-button[data-id="signIn"]')
            sign_in_button.click()

            try:
                page.wait_for_url(
                    "**/s/myjobs", timeout=BROWSER_TIMEOUT_MS / 2
                )
                logger.info("Post-login URL reached or network idle.")
            except PlaywrightTimeoutError:
                error_locator = page.locator('div[role="alert"]:visible, .login-error:visible')
                if error_locator.is_visible(timeout=2000):
                    error_text = (
                        error_locator.first.text_content(timeout=1000)
                        or "[Could not get error text]"
                    )
                    logger.error(f"Login failed. Detected error message: {error_text.strip()}")
                    with contextlib.suppress(Exception):
                        if page: # Check if page exists before screenshot
                            page.screenshot(path="playwright_login_error.png")
                    return None
                else:
                    logger.warning(
                        "Timeout waiting for post-login confirmation, proceeding cautiously."
                    )
            except Exception as wait_err:
                logger.error(f"Error during post-login wait: {wait_err}")
                return None

            # Get cookies
            playwright_cookies = context.cookies() # Type is List[Cookie] according to linter
            if not playwright_cookies:
                logger.error("Failed to retrieve cookies after login attempt.")
                return None

            # Explicitly convert List[Cookie] to list[dict[str, Any]] using dict access
            cookies: list[dict[str, Any]] = [
                {
                    key: cookie[key] # Use dictionary-style access
                    for key in ["name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite"]
                }
                for cookie in playwright_cookies
            ]

            logger.info(f"Login successful, {len(cookies)} cookies obtained.")
            return cookies, session_user_agent
        except PlaywrightTimeoutError as te:
            logger.error(f"Timeout error during Playwright operation: {te}")
            # page = None # Ensure page is defined in this block - already defined above
            with contextlib.suppress(Exception):
                if page: # Check if page exists before screenshot
                    page.screenshot(path="playwright_timeout_error.png")
            return None
        except PlaywrightError as pe:
            logger.error(f"Playwright specific error during login: {pe}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during login process: {e}", exc_info=True)
            return None
        finally:
            if context:
                context.close()
            if browser:
                browser.close()


def validate_session(cookies_list: list[dict[str, Any]], user_agent: str) -> bool:
    # ... (implementation - looks ok) ...
    logger.info("Validating session cookies via API")
    url = "https://www.roberthalf.com/bin/jobSearchServlet"
    cookie_dict = {cookie["name"]: cookie["value"] for cookie in cookies_list}
    headers = {  # ... headers ...
        "accept": "application/json, text/plain, */*",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json",
        "origin": "https://www.roberthalf.com",
        "referer": "https://www.roberthalf.com/us/en/jobs",
        "user-agent": user_agent,
    }
    payload = {  # ... minimal payload ...
        "country": "us",
        "keywords": "",
        "location": "",
        "pagenumber": 1,
        "pagesize": 1,
        "lobid": ["RHT"],
        "source": ["Salesforce"],
    }
    try:
        response = requests.post(
            url, headers=headers, cookies=cookie_dict, json=payload, timeout=REQUEST_TIMEOUT_SECONDS
        )
        if 200 <= response.status_code < 300:
            try:
                response.json()  # Check if response is valid JSON
                logger.info("Session validation successful (API responded with JSON)")
                return True
            except json.JSONDecodeError:
                logger.warning(
                    f"Session validation failed: API status {response.status_code} but response was not JSON."
                )
                return False
        else:
            logger.warning(f"Session validation failed: Status code {response.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        logger.warning(f"Session validation failed due to network error: {e}")
        return False


def get_or_refresh_session() -> tuple[list[dict[str, Any]], str] | None:  # Use Optional
    """Get existing session data or create a new one if needed."""
    loaded_data = load_session_data()
    if loaded_data:
        # Optionally re-validate here if desired, though expiry check handles most cases
        # if validate_session(*loaded_data):
        #     logger.info("Found existing, validated session data.")
        #     return loaded_data
        # logger.info("Found existing session, but validation failed. Refreshing.")
        logger.info("Found existing session data (expiry check passed).")
        return loaded_data

    logger.info("No valid/unexpired session data found. Performing new login.")
    login_result = login_and_get_session()
    if login_result:
        cookies, user_agent = login_result
        save_session_data(cookies, user_agent)
        return cookies, user_agent
    else:
        logger.error("Failed to obtain a new session after login attempt.")
        return None  # Indicate failure to get a session


def filter_jobs_by_state(jobs: list[dict[str, Any]], state_code: str) -> list[dict[str, Any]]:
    # ... (implementation - looks ok) ...
    filtered_jobs = [
        job
        for job in jobs
        if (job.get("stateprovince") == state_code)
        or (job.get("remote", "").lower() == "yes" and job.get("country", "").lower() == "us")
    ]
    # ... (logging counts) ...
    return filtered_jobs


def fetch_jobs(
    cookies_list: list[dict[str, Any]],
    user_agent: str,
    page_number: int = 1,
    is_remote: bool = False,
) -> dict[str, Any] | None:  # Use Optional
    # ... (implementation - proxy logic looks ok, use Optional return) ...
    url = "https://www.roberthalf.com/bin/jobSearchServlet"
    cookie_dict = {cookie["name"]: cookie["value"] for cookie in cookies_list}
    headers = {  # ... headers ...
        "accept": "application/json, text/plain, */*",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json",
        "origin": "https://www.roberthalf.com",
        "referer": "https://www.roberthalf.com/us/en/jobs",
        "user-agent": user_agent,
    }
    payload = {  # ... full payload ...
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
        "includedoe": "",
    }
    proxies = None
    proxy_config_dict = get_proxy_config()
    if proxy_config_dict:
        # Use .get() for potentially missing keys
        server_url = proxy_config_dict.get("server")
        username = proxy_config_dict.get("username")
        password = proxy_config_dict.get("password")

        if server_url:
            if username and password:
                auth = f"{username}:{password}"
                try:
                    parsed_url = urlparse(server_url)
                    # Ensure scheme is present for requests proxy format
                    scheme = parsed_url.scheme if parsed_url.scheme else "http"
                    proxy_url_with_auth = f"{scheme}://{auth}@{parsed_url.netloc}{parsed_url.path}"
                    proxies = {"http": proxy_url_with_auth, "https": proxy_url_with_auth}
                    logger.debug("Using authenticated proxy for requests")
                except ValueError as e:
                    logger.warning(f"Could not parse proxy server URL '{server_url}': {e}")
            else:
                 # Ensure scheme is present for requests proxy format
                try:
                    parsed_url = urlparse(server_url)
                    scheme = parsed_url.scheme if parsed_url.scheme else "http"
                    proxy_url_no_auth = f"{scheme}://{parsed_url.netloc}{parsed_url.path}"
                    proxies = {"http": proxy_url_no_auth, "https": proxy_url_no_auth}
                    logger.debug("Using proxy for requests (no auth)")
                except ValueError as e:
                     logger.warning(f"Could not parse proxy server URL '{server_url}': {e}")
        else:
            logger.warning("Proxy config dictionary returned, but 'server' key is missing. No proxy used.")

    response = None # Initialize response before try block
    try:
        logger.info(f"Fetching {'remote' if is_remote else 'local'} jobs page {page_number}")
        response = requests.post(
            url,
            headers=headers,
            cookies=cookie_dict,
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
            proxies=proxies,
        )
        response.raise_for_status()
        return response.json()  # Directly return parsed JSON
    except json.JSONDecodeError:
        status_code = response.status_code if response is not None else "N/A"
        response_text = response.text[:200] if response is not None else "N/A"
        logger.warning(
            f"Failed to parse API response as JSON (Status: {status_code}). Body: {response_text}..."
        )
        return None
    except requests.exceptions.HTTPError as http_err:
        status_code = http_err.response.status_code
        if status_code in (401, 403):
            logger.warning(f"HTTP {status_code} error suggests session is invalid.")
        else:
            logger.error(f"HTTP error fetching jobs page {page_number}: {http_err}")
        return None
    except requests.exceptions.RequestException as req_err:
        logger.error(f"Network error fetching jobs page {page_number}: {req_err}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error fetching jobs page {page_number}: {e}", exc_info=True)
        return None


def fetch_with_retry(
    cookies_list: list[dict[str, Any]], user_agent: str, page_number: int, is_remote: bool = False
) -> dict[str, Any] | None:  # Use Optional
    # ... (implementation - looks ok) ...
    base_wait_time = 5
    for attempt in range(MAX_RETRIES):
        result = fetch_jobs(cookies_list, user_agent, page_number, is_remote)
        if result is not None:
            return result
        wait_time = base_wait_time * (2**attempt) + random.uniform(
            0, base_wait_time / 2
        )  # Adjusted jitter
        logger.warning(
            f"API fetch attempt {attempt + 1}/{MAX_RETRIES} failed. Retrying in {wait_time:.2f}s..."
        )
        time.sleep(wait_time)
    logger.error(f"All {MAX_RETRIES} retry attempts failed for page {page_number}.")
    return None


def _generate_html_report(  # Add new_job_ids argument
    jobs_list: list[dict[str, Any]],
    timestamp: str,
    total_found: int,
    state_filter: str,
    job_period: str,
    new_job_ids: set[str],  # Added
) -> str:
    # ... (Existing HTML generation - Consider adding analysis scores here) ...
    # Example: Add a score column or include in expandable description
    num_tx_jobs = len([job for job in jobs_list if job.get("stateprovince") == state_filter])
    num_remote_jobs = len([job for job in jobs_list if job.get("remote", "").lower() == "yes"])
    num_new_jobs = len(new_job_ids)

    # Convert UTC timestamp to CST/CDT
    cst = pytz.timezone("America/Chicago")
    try:
        dt_utc = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        dt_cst = dt_utc.astimezone(cst)
        formatted_timestamp = dt_cst.strftime("%Y-%m-%d %H:%M:%S %Z")
    except ValueError:
        formatted_timestamp = timestamp  # Fallback

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Robert Half Job Report ({state_filter}) - {formatted_timestamp}</title>
    <style>
        /* Basic styles from the old version */
        body {{ font-family: sans-serif; margin: 20px; }}
        h1 {{ color: #333; }}
        p {{ color: #555; }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
        }}
        th, td {{
            border: 1px solid #ddd;
            padding: 8px;
            text-align: left;
            vertical-align: top;
        }}
        th {{ background-color: #f2f2f2; }}
        tr:nth-child(even) {{ background-color: #f9f9f9; }}
        a {{ color: #007bff; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        .pay-rate, .location {{ white-space: nowrap; }}

        /* Styling for the expandable content */
        .job-row {{
            cursor: pointer;
        }}
        .job-row:hover {{
            background-color: #f0f8ff; /* AliceBlue on hover */
        }}
        .job-description {{
            padding: 15px;
            background-color: #fff;
            border-top: none;
            margin-top: 0;
        }}
        .description-container {{
            padding: 0;
            border-top: none;
            background-color: #fff;
        }}
        .job-row .expander {{
            display: inline-block;
            width: 20px;
            height: 20px;
            text-align: center;
            line-height: 20px;
            border-radius: 3px;
            margin-right: 8px;
            background-color: #f2f2f2; /* Light gray background for expander */
            font-weight: bold;
            font-size: 14px;
        }}

        /* Styling for new job highlighting */
        .new-job .title-cell {{
            background-color: #f0fff0; /* Honeydew for new job title cell */
        }}
        .new-tag {{
            display: inline-block;
            background-color: #28a745; /* Green background for NEW tag */
            color: white;
            padding: 2px 6px;
            font-size: 0.75em;
            font-weight: bold;
            border-radius: 4px;
            margin-right: 5px;
            vertical-align: middle;
        }}

        /* Styles for LLM analysis elements */
        .score-badge {{ /* Style for score */
            display: inline-block; padding: 2px 5px; margin-right: 5px;
            font-size: 0.8em; border-radius: 4px; color: white;
        }}
        .score-high {{ background-color: #28a745; }} /* Green */
        .score-medium {{ background-color: #ffc107; color: #333; }} /* Yellow */
        .score-low {{ background-color: #6c757d; }} /* Gray */
        .recommendation {{ font-weight: bold; margin-right: 5px; }}
        .rec-apply {{ color: #28a745; }}
        .rec-consider {{ color: #ffc107; }}
        .rec-skip {{ color: #6c757d; }}
        .analysis-summary {{ font-style: italic; color: #555; font-size: 0.9em; margin-top: 5px; }}
    </style>
</head>
<body>
    <h1>Robert Half Job Report</h1>
    <p>Generated: {formatted_timestamp}</p>
    <p>Filters: State = {state_filter}, Posted Within = {job_period.replace("_", " ")}</p>
    <p>Found {num_tx_jobs} jobs in {state_filter} and {num_remote_jobs} remote jobs (Total Unique: {len(jobs_list)}). Identified <span style="background-color: #f0fff0; padding: 1px 3px; border: 1px solid #ccc;">{num_new_jobs} New Jobs</span> since last CSV entry. API reported {total_found} total jobs matching period.</p>

    <table id="jobTable">
        <thead>
            <tr>
                <th>Match / Title</th>
                <th>Location</th>
                <th>Pay Rate</th>
                <th>Job ID</th>
                <th>Posted Date (CST/CDT)</th>
            </tr>
        </thead>
        <tbody>
"""
    # Sort jobs: New > High Score > Date Posted > Title
    jobs_list.sort(
        key=lambda x: (
            x.get("is_new", False),
            # --- MODIFIED SCORE LOGIC ---
            (
                # Use walrus operator (:=) to assign analysis dict if it exists
                # Check analysis exists, has no 'error', and calculated score is not None
                analysis.get("final_score_calculated")
                if (analysis := x.get("match_analysis"))
                and "error" not in analysis
                and analysis.get("final_score_calculated") is not None
                # Otherwise, default to -1 (or 0 if you prefer)
                else -1
            ),
            # --- END MODIFIED SCORE LOGIC ---
            x.get("date_posted", "1970-01-01T00:00:00Z"),
            x.get("jobtitle", ""),
        ),
        reverse=True,
    )

    for idx, job in enumerate(jobs_list, 1):
        # ... (extract title, location, pay, etc.) ...
        title = job.get("jobtitle", "N/A")
        city = job.get("city", "N/A")
        state = job.get("stateprovince", "")
        is_remote = job.get("remote", "").lower() == "yes"
        job_id = job.get("unique_job_number", "N/A")
        job_url = job.get("job_detail_url", "#")
        location_str = f"{city}, {state}" if not is_remote else "Remote (US)"
        pay_rate_str = "N/A"  # ... (pay rate formatting logic) ...
        if pay_min_str := job.get("payrate_min"):
            pay_max_str = job.get("payrate_max")
            pay_period = job.get("payrate_period", "").lower()
            if pay_max_str and pay_period:
                try:
                    pay_min = int(float(pay_min_str))
                    pay_max = int(float(pay_max_str))
                    pay_rate_str = f"${pay_min:,} - ${pay_max:,} / {pay_period}"
                except (ValueError, TypeError):
                    pay_rate_str = f"{pay_min_str} - {pay_max_str} ({pay_period})"

        posted_date_str = "N/A"  # ... (date formatting logic) ...
        if date_posted := job.get("date_posted"):
            try:
                posted_dt = datetime.fromisoformat(date_posted.replace("Z", "+00:00")).astimezone(
                    cst
                )
                posted_date_str = posted_dt.strftime("%Y-%m-%d %H:%M %Z")
            except ValueError:
                posted_date_str = date_posted

        is_new = job.get("is_new", False)  # Use the flag added earlier
        new_indicator_html = '<span class="new-tag">NEW</span> ' if is_new else ""
        row_class = "job-row new-job" if is_new else "job-row"

        # Inside the loop in _generate_html_report (around line 583)

        # --- Add Analysis Info ---
        analysis_html = ""
        analysis = job.get("match_analysis")  # analysis can be None or a dict

        # *** ADJUSTED CHECK ***
        # Check if analysis is a dictionary AND does not contain the 'error' key
        if isinstance(analysis, dict) and "error" not in analysis:
            # Now we know analysis is a dict, safe to use .get()
            score = analysis.get("final_score_calculated")
            tier2_result = analysis.get("tier2_result")  # Get the tier2 dict (or None)

            # Initialize defaults
            score_badge = ""
            reco_html = ""
            analysis_summary_text = ""

            # --- Score Badge ---
            if score is not None:
                score_class = "score-low"
                if score >= 75:
                    score_class = "score-high"
                elif score >= 60:
                    score_class = "score-medium"
                score_badge = f'<span class="score-badge {score_class}">{score:.0f}</span>'
            else:
                # Indicate score calculation error if analysis exists but score is None
                score_badge = '<span style="color: orange; font-size: 0.8em;">Score N/A</span> '

            # --- Recommendation ---
            # Check if tier2_result is also a dictionary before accessing it
            if isinstance(tier2_result, dict):
                reco = tier2_result.get("overall_recommendation", "")
                reco_class = "rec-skip"
                reco_text = "Skip"
                if reco == "apply":
                    reco_class, reco_text = "rec-apply", "Apply!"
                elif reco == "consider":
                    reco_class, reco_text = "rec-consider", "Consider"
                reco_html = f'<span class="recommendation {reco_class}">{reco_text}</span>'

                summary = tier2_result.get("summary", "")
                if summary:
                    analysis_summary_text = f'<p class="analysis-summary"><strong>AI Summary:</strong> {summary}</p><hr>'
            else:
                # Handle case where Tier 2 failed or was skipped (analysis exists but tier2_result is None)
                reco_html = '<span style="color: gray; font-size: 0.8em;">No Reco</span> '
                analysis_summary_text = (
                    '<p class="analysis-summary"><em>Tier 2 analysis not available.</em></p><hr>'
                )

            analysis_html = f"{reco_html}{score_badge}"  # Combine badge and score

        elif isinstance(analysis, dict) and "error" in analysis:
            # Handle the case where the analysis dict itself indicates an error
            analysis_html = '<span style="color: red; font-size: 0.8em;">Analysis Error</span>'
            analysis_summary_text = f'<p class="analysis-summary"><em>Error during analysis: {analysis.get("error", "Unknown")}</em></p><hr>'
        else:
            # Handle case where analysis is None (job wasn't analyzed)
            analysis_html = (
                ""  # Or maybe '<span style="color: gray; font-size: 0.8em;">Not Analyzed</span>'
            )
            analysis_summary_text = ""  # No summary needed

        # --- End Analysis Info ---

        # Main job data row - Prepend analysis_html
        html_content += f"""
            <tr class="{row_class}" data-job-id="{idx}">
                <td class="title-cell"><span class="expander">+</span> {analysis_html}{new_indicator_html}<a href="{job_url}" target="_blank">{title}</a></td>
                <td class="location">{location_str}</td>
                <td class="pay-rate">{pay_rate_str}</td>
                <td>{job_id}</td>
                <td>{posted_date_str}</td>
            </tr>"""

        # Description row (hidden by default) - Add analysis summary
        description_html = job.get("description", "No description available.")
        # Prepend the summary text generated earlier
        description_html = analysis_summary_text + description_html

        html_content += f"""
            <tr class="description-row" id="job-{idx}" style="display:none;">
                <td colspan="5" class="description-container">
                    <div class="job-description">
                        {description_html}
                    </div>
                </td>
            </tr>
"""
    html_content += """
        </tbody>
    </table>
    <script>
        // ... existing script ...
        document.addEventListener('DOMContentLoaded', function() {
            const jobRows = document.querySelectorAll('.job-row');
            jobRows.forEach(row => {
                row.addEventListener('click', function(event) {
                    // Prevent toggling if clicking on the link itself
                    if (event.target.tagName === 'A') {
                        return;
                    }
                    const jobId = this.getAttribute('data-job-id');
                    const descriptionRow = document.getElementById('job-' + jobId);
                    const expander = this.querySelector('.expander');

                    if (descriptionRow && expander) { // Check if elements exist
                         if (descriptionRow.style.display === 'none') {
                            descriptionRow.style.display = 'table-row';
                            expander.textContent = '-';
                         } else {
                            descriptionRow.style.display = 'none';
                            expander.textContent = '+';
                         }
                    }
                });
            });
        });
    </script>
</body>
</html>
"""
    return html_content


def _find_latest_json_report(
    output_dir: Path, filename_prefix: str, state_filter: str
) -> Path | None:
    # ... (implementation - looks ok) ...
    try:
        pattern = f"{filename_prefix}_{state_filter.lower()}_jobs_*.json"
        files = sorted(output_dir.glob(pattern), key=os.path.getmtime, reverse=True)
        if files:
            logger.info(f"Found latest previous report file: {files[0].name}")
            return files[0]
        logger.info(f"No previous report files found matching pattern '{pattern}'")
        return None
    except Exception as e:
        logger.error(f"Error finding latest JSON report: {e}")
        return None


def _load_job_ids_from_json(json_file_path: Path) -> set[str]:
    # ... (implementation - looks ok) ...
    job_ids: set[str] = set()
    if not json_file_path or not json_file_path.exists():
        return job_ids
    try:
        with open(json_file_path, encoding="utf-8") as f:
            data = json.load(f)
        for job in data.get("jobs", []):
            if job_id := job.get("unique_job_number"):
                job_ids.add(job_id)
        logger.info(f"Loaded {len(job_ids)} job IDs from previous report: {json_file_path.name}")
    except (FileNotFoundError, json.JSONDecodeError, Exception) as e:
        logger.warning(f"Could not load/parse previous report {json_file_path.name}: {e}")
    return job_ids


def _run_git_command(
    command: list[str], cwd: Path, sensitive: bool = False
) -> tuple[bool, str, str]:
    # ... (implementation - looks ok) ...
    cmd_display = " ".join(command) if not sensitive else f"{command[0]} [args hidden]"
    try:
        logger.debug(f"Running command: {cmd_display} in {cwd}")
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
        stdout = result.stdout.strip() if result.stdout else ""
        stderr = result.stderr.strip() if result.stderr else ""
        if result.returncode == 0:
            logger.debug(
                f"Git command successful. stdout: {stdout}" if stdout else "Git command successful."
            )
            if stderr:
                logger.warning(f"Git command stderr: {stderr}")
            return True, stdout, stderr
        else:
            logger.error(
                f"Git command failed ({result.returncode}): {cmd_display}\nStdout: {stdout}\nStderr: {stderr}"
            )
            return False, stdout, stderr
    except FileNotFoundError:
        logger.error(f"Git command failed: '{command[0]}' not found.")
        return False, "", "Git executable not found"
    except Exception as e:
        logger.error(f"Error running git command {cmd_display}: {e}", exc_info=True)
        return False, "", f"Unexpected error: {e}"


def _commit_and_push_report(
    html_file_path: Path, timestamp: str, config: dict[str, Any]
) -> None:  # Use Dict
    # ... (implementation - looks ok, uses _run_git_command) ...
    repo_dir = Path.cwd()
    commit_message = f"Update job report for {config.get('FILTER_STATE', 'N/A')} - {timestamp}"
    html_rel_path_str = str(html_file_path)  # Use the path relative to cwd directly

    # Check Git status first
    status_ok, stdout, _ = _run_git_command(
        ["git", "status", "--porcelain", html_rel_path_str], cwd=repo_dir
    )
    if status_ok and not stdout:
        logger.info(f"No changes detected in {html_rel_path_str}. Skipping Git commit/push.")
        return

    # Add, Commit, Push logic using _run_git_command...
    logger.info(f"Changes detected in {html_rel_path_str}. Proceeding with Git operations.")
    add_ok, _, _ = _run_git_command(["git", "add", html_rel_path_str], cwd=repo_dir)
    if not add_ok:
        return  # Error logged in helper

    commit_ok, _, _ = _run_git_command(["git", "commit", "-m", commit_message], cwd=repo_dir)
    if not commit_ok:
        return  # Error logged in helper

    # Push logic (token or default)
    git_token = config.get("GITHUB_ACCESS_TOKEN")
    push_command = ["git", "push"]
    sensitive_push = False
    if git_token:
        remote_url_ok, remote_url, _ = _run_git_command(
            ["git", "remote", "get-url", "--push", "origin"], cwd=repo_dir
        )
        if remote_url_ok and remote_url and remote_url.startswith("https"):
            branch_ok, current_branch, _ = _run_git_command(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir
            )
            if branch_ok and current_branch:
                try:
                    parsed = urlparse(remote_url)
                    auth_url = f"https://{git_token}@{parsed.netloc}{parsed.path}"
                    push_command = ["git", "push", auth_url, current_branch]
                    sensitive_push = True
                    logger.info("Using token authentication for git push.")
                except Exception as e:
                    logger.warning(
                        f"Failed to construct authenticated push URL: {e}. Falling back."
                    )
            else:
                logger.warning("Could not get current branch. Falling back.")
        else:
            logger.warning("Remote URL is not HTTPS or not found. Falling back.")
    else:
        logger.info("No GitHub token. Using default git push.")

    push_ok, _, _ = _run_git_command(push_command, cwd=repo_dir, sensitive=sensitive_push)
    if push_ok:
        logger.info("Successfully pushed updated job report.")
    # Error logged in helper if push fails


def save_job_results(
    jobs_list: list[dict[str, Any]],
    total_found: int,
    config: dict[str, Any],
    analyzer: JobMatchAnalyzerV2 | None,
    new_job_ids: set[str],
    analyze_all: bool = False,  # Add the argument here
    filename_prefix: str = "roberthalf",
    llm_debug: bool = False,  # Add the llm_debug argument here
) -> None:
    """Save the final list of jobs to JSON and generate/commit/push an HTML report."""
    output_dir = OUTPUT_DIR
    docs_dir = DOCS_DIR
    timestamp_dt = datetime.now(UTC)
    timestamp_str = timestamp_dt.strftime("%Y%m%d_%H%M%S")
    iso_timestamp_str = timestamp_dt.isoformat(timespec="seconds").replace("+00:00", "Z")

    state_filter = config.get("FILTER_STATE", "N/A")
    job_period = config.get("JOB_POST_PERIOD", "N/A")
    test_mode = config.get("TEST_MODE", False)
    pushover_enabled = config.get("PUSHOVER_ENABLED", False)
    github_pages_url = config.get("GITHUB_PAGES_URL")

    # Count TX and remote jobs after potential filtering
    tx_jobs = [job for job in jobs_list if job.get("stateprovince") == state_filter]
    remote_jobs = [job for job in jobs_list if job.get("remote", "").lower() == "yes"]

    # --- Add 'is_new' flag ---
    for job in jobs_list:
        job["is_new"] = job.get("unique_job_number") in new_job_ids

    # --- Evaluate Job Matches ---
    if analyzer:
        # Informative log based on the flag
        if analyze_all:
            log_message = f"--- Starting AI Job Match Evaluation (V2) for ALL {len(jobs_list)} jobs (--analyze-all active) ---"
        else:
            log_message = (
                f"--- Starting AI Job Match Evaluation (V2) for {len(new_job_ids)} new jobs ---"
            )
        logger.info(log_message)

        evaluated_count = 0
        for job in jobs_list:
            job_id = job.get("unique_job_number")
            # *** THIS IS THE KEY CHANGE ***
            # Analyze if it's new OR if test_mode is on OR if the analyze_all flag is passed
            should_analyze = job.get("is_new") or test_mode or analyze_all

            if should_analyze:
                # Add flag status to debug log for clarity
                logger.debug(
                    f"Analyzing job {job_id} (is_new={job.get('is_new', False)}, test_mode={test_mode}, analyze_all={analyze_all})..."
                )
                match_analysis = analyzer.analyze_job(job)  # Call the V2 analyzer
                job["match_analysis"] = match_analysis  # Store the full analysis dict
                if match_analysis and "error" not in match_analysis:
                    evaluated_count += 1
                # Add a small delay between *full analyses* if needed
                time.sleep(0.5)  # Shorter delay as main calls have internal waits
            else:
                job["match_analysis"] = None  # Mark jobs not analyzed in this run

        logger.info(f"--- Finished AI Job Match Evaluation ({evaluated_count} jobs analyzed) ---")
    else:
        logger.info(
            "AI Matching is disabled or analyzer failed to initialize. Skipping evaluation."
        )
        for job in jobs_list:
            job["match_analysis"] = None  # Ensure key exists

    # --- Save JSON Results ---
    json_filename = f"{filename_prefix}_{state_filter.lower()}_jobs_{timestamp_str}.json"
    json_output_file_path = output_dir / json_filename
    results_data = {
        "jobs": jobs_list,  # Includes 'is_new' and 'match_analysis'
        "timestamp": iso_timestamp_str,
        f"total_{state_filter.lower()}_jobs": len(tx_jobs),
        "total_remote_jobs": len(remote_jobs),
        "total_new_jobs": len(new_job_ids),
        "total_jobs_found_in_period": total_found,
        "job_post_period_filter": job_period,
        "state_filter": state_filter,
        "status": "Completed",
    }
    try:
        with open(json_output_file_path, "w", encoding="utf-8") as f:
            json.dump(results_data, f, indent=2, ensure_ascii=False)
        logger.info(f"Saved {len(jobs_list)} jobs results to {json_output_file_path.resolve()}")
    except Exception as e:
        logger.error(f"Failed to save JSON results: {e}")

    # --- Generate and Save HTML Report ---
    html_filename = "jobs.html"
    html_output_file_path = docs_dir / html_filename
    try:
        # Pass new_job_ids to the HTML generator
        html_content = _generate_html_report(
            jobs_list, iso_timestamp_str, total_found, state_filter, job_period, new_job_ids
        )
        with open(html_output_file_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        logger.info(f"Generated HTML report at: {html_output_file_path.resolve()}")

        # --- Commit and Push HTML Report ---
        # Only commit if changes detected (handled internally) or in test mode forces it?
        # Current logic pushes if jobs_list > 0 or test_mode
        if len(jobs_list) > 0 or test_mode:
            _commit_and_push_report(html_output_file_path, timestamp_str, config)
        else:
            logger.info("No jobs found and not in test mode. Skipping Git commit/push.")

    except Exception as e:
        logger.error(f"Failed to generate/save/push HTML report: {e}", exc_info=True)

    # --- Send Notification ---
    if pushover_enabled:
        jobs_to_notify = []
        if analyzer:
            # Filter based on the final calculated score and threshold
            final_threshold = analyzer.final_threshold
            for job in jobs_list:
                analysis = job.get("match_analysis")
                # Notify if NEW and analysis successful and meets final threshold
                if (
                    job.get("is_new")
                    and analysis
                    and "error" not in analysis
                    and analysis.get("meets_final_threshold", False)
                ):
                    jobs_to_notify.append(job)
            logger.info(
                f"Found {len(jobs_to_notify)} new jobs meeting final threshold (>{final_threshold}) to notify about."
            )
        else:
            # Fallback: Notify about all NEW jobs if matching disabled/failed
            jobs_to_notify = [job for job in jobs_list if job.get("is_new")]
            if not config.get("MATCHING_ENABLED"):
                logger.info("AI Matching disabled. Will notify about all new jobs.")
            else:
                logger.warning("AI Matching failed. Falling back to notifying about all new jobs.")

        if len(jobs_to_notify) > 0 or test_mode:
            # Sort by calculated score (descending), handle None scores
            jobs_to_notify.sort(
                key=lambda x: x.get("match_analysis", {}).get("final_score_calculated", -1)
                if x.get("match_analysis")
                else -1,
                reverse=True,
            )

            # Format notification message
            job_details_notify = []
            max_jobs_in_notification = 5
            for job in jobs_to_notify[:max_jobs_in_notification]:
                title = job.get("jobtitle", "N/A")
                city = job.get("city", "N/A")
                state = job.get("stateprovince", "")
                is_remote = job.get("remote", "").lower() == "yes"
                location = "Remote" if is_remote else f"{city}, {state}"
                analysis = job.get("match_analysis")
                score_str = ""
                summary_str = ""
                reco_str = ""

                if analysis and "error" not in analysis:
                    score = analysis.get("final_score_calculated")
                    # Check if score calculation was successful
                    if score is not None:
                        score_color = (
                            "#28a745" if score >= 75 else ("#ffc107" if score >= 60 else "#6c757d")
                        )
                        score_str = f'<b><font color="{score_color}">({score:.0f}/100)</font></b> '
                    else:
                        score_str = "<b>(ERR)</b> "  # Indicate score calc error

                    if tier2 := analysis.get("tier2_result"):
                        summary = tier2.get("summary", "")
                        if summary:
                            summary_str = f"\n  <i>{summary}</i>"
                        reco = tier2.get("overall_recommendation", "")
                        if reco == "apply":
                            reco_str = '<font color="#28a745">Apply!</font> '
                        elif reco == "consider":
                            reco_str = '<font color="#ffc107">Consider</font> '
                        else:
                            reco_str = '<font color="#6c757d">Skip</font> '
                    else:
                        summary_str = "\n  <i>Tier 2 analysis failed.</i>"  # Indicate T2 fail
                        reco_str = '<font color="#dc3545">Error</font> '

                elif analysis and "error" in analysis:
                    score_str = "<b>(ERR)</b> "
                    summary_str = f"\n  <i>Error: {analysis.get('error')}</i>"
                    reco_str = '<font color="#dc3545">Error</font> '

                detail = f"• {reco_str}{score_str}{title} ({location})"
                # Add pay rate
                pay_min_str = job.get("payrate_min")
                pay_max_str = job.get("payrate_max")
                pay_period = job.get("payrate_period", "").lower()
                if pay_min_str and pay_max_str and pay_period:
                    with contextlib.suppress(ValueError, TypeError):
                        detail += f"\n  ${int(float(pay_min_str)):,} - ${int(float(pay_max_str)):,}/{pay_period}"

                detail += summary_str  # Add summary
                job_details_notify.append(detail)

            details_text_notify = "\n".join(job_details_notify)
            remaining_notify = len(jobs_to_notify) - len(job_details_notify)

            # Construct notification message
            notification_title = f"Robert Half Job Matches ({len(jobs_to_notify)} new relevant)"
            if test_mode and len(jobs_to_notify) == 0:
                message = (
                    "🧪 TEST MODE: Simulating high-scoring job notification!\n\n"
                    '• <font color="#28a745">Apply!</font> <b><font color="#28a745">(85/100)</font></b> Test Full Stack (Dallas)\n'
                    "  $120,000 - $150,000/yearly\n  <i>Good match.</i>"
                    "\n\nClick link."
                )
            else:
                if analyzer:
                    message = f"Found {len(jobs_to_notify)} NEW relevant jobs! (>{analyzer.final_threshold}/100)"
                else:  # Fallback
                    message = f"Found {len(jobs_to_notify)} NEW jobs! (AI Matcher disabled/failed)"

                if job_details_notify:
                    message += f"\n\nTop Matches:\n{details_text_notify}"
                if remaining_notify > 0:
                    message += f"\n\n...and {remaining_notify} more relevant jobs"
                message += "\n\nClick link for full report."

            # Send notification
            try:
                pushover_url = None
                pushover_url_title = None
                if not github_pages_url:
                    logger.warning(
                        "GITHUB_PAGES_URL not set. Pushover notification will lack report URL."
                    )
                # Add placeholder check if desired
                # elif "YOUR_USERNAME" in github_pages_url or "YOUR_REPO_NAME" in github_pages_url:
                #     logger.warning("GITHUB_PAGES_URL may contain placeholders.")
                #     pushover_url = github_pages_url
                #     pushover_url_title = f"View Full {state_filter}/Remote Job List"
                else:
                    pushover_url = github_pages_url
                    pushover_url_title = f"View Full {state_filter}/Remote Job List"

                send_pushover_notification(
                    message=message,
                    user="Joe",  # Make configurable?
                    title=notification_title,
                    url=pushover_url,
                    url_title=pushover_url_title,
                    html=1,
                )
                logger.info(f"Pushover notification sent for {len(jobs_to_notify)} jobs.")
            except Exception as notify_err:
                logger.error(f"Failed to send push notification: {notify_err}")
        else:
            # Log why notification wasn't sent
            if not test_mode and len(jobs_to_notify) == 0:
                logger.info("No new jobs met the final notification threshold. Skipping Pushover.")
            elif test_mode and len(jobs_to_notify) == 0:
                logger.info("Test mode active, but no jobs to notify about. Skipping Pushover.")

    elif not pushover_enabled:
        logger.info("Pushover notifications are disabled.")


def scrape_roberthalf_jobs(analyze_all: bool = False, llm_debug: bool = False) -> None:
    """Main function to orchestrate the Robert Half job scraping."""
    logger.info("--- Starting Robert Half Job Scraper ---")
    if analyze_all:
        logger.warning(
            "!!! --analyze-all flag is active: AI analysis will run on ALL jobs found in this report (potential extra cost/time) !!!"
        )
    if llm_debug:
        logger.warning(
            "!!! --llm-debug flag is active: Enabling verbose logging for LLM analysis (ensure log level is DEBUG). !!!"
        )
    start_time = time.time()

    # Initialize Analyzer *before* the main try block
    analyzer: JobMatchAnalyzerV2 | None = None  # Type hint
    if config.get("MATCHING_ENABLED"):
        logger.info("AI Matching is enabled, initializing analyzer...")
        try:
            # Pass the llm_debug flag to the analyzer
            analyzer = JobMatchAnalyzerV2(config, llm_debug=llm_debug)
            # Crucially, check if the profile actually loaded within the analyzer
            if not analyzer.candidate_profile:
                logger.error(
                    "Analyzer initialized, but profile loading failed. Disabling matching for this run."
                )
                analyzer = None  # Set analyzer to None if profile is missing
        except ValueError as e:  # Catch API key error etc.
            logger.error(f"Failed to initialize JobMatchAnalyzerV2: {e}. Matching disabled.")
        except Exception as e:
            logger.error(
                f"Unexpected error initializing JobMatchAnalyzerV2: {e}. Matching disabled.",
                exc_info=True,
            )
    else:
        logger.info("AI Matching is disabled in configuration.")

    try:
        # --- Get Session ---
        session_info = get_or_refresh_session()
        if not session_info:
            # Error already logged in get_or_refresh_session
            raise RuntimeError("Failed to establish a valid session. Exiting.")
        session_cookies, session_user_agent = session_info

        all_filtered_jobs = []
        total_jobs_api_reported = 0

        # --- Fetch Jobs (Local and Remote) ---
        for is_remote in [False, True]:
            page_number = 1
            jobs_found_this_type = None
            while True:
                job_type_str = "Remote" if is_remote else "Local"
                logger.info(f"--- Processing {job_type_str} Page {page_number} ---")
                response_data = fetch_with_retry(
                    session_cookies, session_user_agent, page_number, is_remote
                )
                if not response_data:
                    logger.warning(
                        f"Fetch failed for {job_type_str} page {page_number}. Validating session."
                    )
                    if not validate_session(session_cookies, session_user_agent):
                        raise RuntimeError("Session became invalid during pagination.")
                    else:
                        raise RuntimeError(
                            f"Failed to fetch {job_type_str} page {page_number} despite valid session."
                        )

                if jobs_found_this_type is None:
                    try:
                        current_found = int(response_data.get("found", 0))
                        jobs_found_this_type = current_found
                        total_jobs_api_reported += current_found
                        logger.info(
                            f"API reports {current_found} total {job_type_str} jobs for period '{JOB_POST_PERIOD}'"
                        )
                    except (ValueError, TypeError):
                        logger.warning("Could not parse 'found' count.")
                        jobs_found_this_type = -1

                jobs_on_page = response_data.get("jobs", [])
                if not jobs_on_page:
                    logger.info(f"No more {job_type_str} jobs on page {page_number}.")
                    break

                logger.info(
                    f"Received {len(jobs_on_page)} {job_type_str} jobs on page {page_number}."
                )
                state_jobs_on_page = filter_jobs_by_state(jobs_on_page, FILTER_STATE)
                all_filtered_jobs.extend(state_jobs_on_page)

                if len(jobs_on_page) < 25:  # Assuming page size is 25
                    logger.info("Received less than page size. Assuming last page.")
                    break
                if jobs_found_this_type >= 0:  # Check pagination limit
                    max_pages_expected = (jobs_found_this_type + 24) // 25
                    if page_number >= max_pages_expected:
                        logger.info(
                            f"Reached expected max page number ({page_number}/{max_pages_expected}). Stopping."
                        )
                        break

                page_number += 1
                page_delay = random.uniform(PAGE_DELAY_MIN, PAGE_DELAY_MAX)
                logger.debug(f"Waiting {page_delay:.2f}s before next page.")
                time.sleep(page_delay)

            if not is_remote:
                switch_delay = random.uniform(PAGE_DELAY_MIN * 1.2, PAGE_DELAY_MAX * 1.2)
                logger.info(f"Finished local. Switching to remote. Waiting {switch_delay:.2f}s...")
                time.sleep(switch_delay)

        # --- Deduplicate Jobs ---
        unique_jobs_dict = {}
        duplicates_found = 0
        for job in all_filtered_jobs:
            job_id = job.get("unique_job_number")
            if job_id:
                if job_id not in unique_jobs_dict:
                    unique_jobs_dict[job_id] = job
                else:
                    duplicates_found += 1
            else:
                logger.warning("Job found without unique_job_number.")
        unique_job_list = list(unique_jobs_dict.values())
        logger.info(
            f"Total unique jobs found: {len(unique_job_list)} (Removed {duplicates_found} duplicates)."
        )

        # --- Process and Save Results ---
        existing_job_ids_csv = read_existing_job_data(CSV_FILE_PATH)
        new_job_ids = {
            job.get("unique_job_number") for job in unique_job_list if job.get("unique_job_number")
        } - existing_job_ids_csv
        logger.info(f"Identified {len(new_job_ids)} new jobs compared to CSV history.")

        # Pass analyzer instance, new_job_ids, AND the analyze_all flag to save_job_results
        save_job_results(
            unique_job_list,
            total_jobs_api_reported,
            config,
            analyzer,
            new_job_ids,
            analyze_all=analyze_all,
            llm_debug=llm_debug,
        )
        append_job_data_to_csv(unique_job_list, CSV_FILE_PATH, existing_job_ids_csv)

    except RuntimeError as rt_err:
        logger.critical(f"Stopping run due to runtime error: {rt_err}")
    except ValueError as val_err:  # Catch config errors like missing credentials
        logger.critical(f"Configuration error: {val_err}")
    except Exception as e:
        logger.critical(f"An unexpected critical error occurred: {e}", exc_info=True)
    finally:
        end_time = time.time()
        logger.info("--- Robert Half Job Scraper Finished ---")
        logger.info(f"Total execution time: {end_time - start_time:.2f} seconds")


# --- CSV Functions ---
def read_existing_job_data(csv_file_path: Path) -> set[str]:
    """Reads existing Job IDs from the CSV file."""
    existing_jobs = set()
    if not csv_file_path.exists():
        logger.info(f"CSV file {csv_file_path} not found. Starting fresh.")
        return existing_jobs
    try:
        with open(csv_file_path, newline="", encoding="utf-8") as csvfile:
            # Handle potential empty file or header-only file
            # Peek at the first line to check for content beyond header
            first_line = csvfile.readline()
            if not first_line:  # Empty file
                logger.info(f"CSV file {csv_file_path} is empty.")
                return existing_jobs
            csvfile.seek(0)  # Reset cursor to beginning

            reader = csv.DictReader(csvfile)
            # Check if 'Job ID' column exists (and fieldnames is not None)
            if not reader.fieldnames or "Job ID" not in reader.fieldnames:
                logger.error(
                    f"CSV file {csv_file_path} is missing required header ('Job ID') or is malformed. Cannot track existing jobs."
                )
                return existing_jobs

            for row in reader:
                if job_id := row.get("Job ID"):  # Check if Job ID is not empty
                    existing_jobs.add(job_id)
        logger.info(f"Read {len(existing_jobs)} existing job IDs from {csv_file_path}")
    except Exception as e:
        logger.error(f"Error reading existing job data from {csv_file_path}: {e}")
    return existing_jobs


def append_job_data_to_csv(
    jobs: list[dict[str, Any]], csv_file_path: Path, existing_job_ids: set[str]
) -> None:
    """Appends only *new* job data to the CSV file."""
    fieldnames = [
        "Job ID",
        "Job Title",
        "Date First Seen (UTC)",
        "Date Posted",
        "Location",
        "Company Name",
        "Pay Rate",
        "Job URL",
    ]
    new_jobs_added_count = 0
    is_new_file = not csv_file_path.exists() or csv_file_path.stat().st_size == 0

    try:
        with open(csv_file_path, mode="a", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            if is_new_file:
                writer.writeheader()
                logger.info(f"Created or wrote header to new CSV: {csv_file_path}")

            for job in jobs:
                job_id = job.get("unique_job_number")
                # Check if job_id exists AND if it's not already in the set read at the start
                if job_id and job_id not in existing_job_ids:
                    # Format Pay Rate carefully
                    pay_min_str = job.get("payrate_min")
                    pay_max_str = job.get("payrate_max")
                    pay_period = job.get("payrate_period", "")
                    pay_rate = "N/A"
                    if pay_min_str and pay_max_str and pay_period:
                        try:
                            pay_rate = f"${int(float(pay_min_str)):,} - ${int(float(pay_max_str)):,}/{pay_period}"
                        except (ValueError, TypeError):
                            pay_rate = f"{pay_min_str}-{pay_max_str}/{pay_period}"

                    writer.writerow(
                        {
                            "Job ID": job_id,
                            "Job Title": job.get("jobtitle", "N/A"),
                            "Date First Seen (UTC)": datetime.now(UTC)
                            .isoformat(timespec="seconds")
                            .replace("+00:00", "Z"),
                            "Date Posted": job.get("date_posted", "N/A"),
                            "Location": f"{job.get('city', 'N/A')}, {job.get('stateprovince', 'N/A')}"
                            if job.get("remote", "").lower() != "yes"
                            else "Remote (US)",
                            "Company Name": job.get(
                                "source", "N/A"
                            ),  # Or a better field if available
                            "Pay Rate": pay_rate,
                            "Job URL": job.get("job_detail_url", "N/A"),
                        }
                    )
                    new_jobs_added_count += 1
                    existing_job_ids.add(
                        job_id
                    )  # Add to set immediately to prevent duplicates within the same run if job appears twice

        if new_jobs_added_count > 0:
            logger.info(f"Appended {new_jobs_added_count} new job entries to {csv_file_path}")

    except Exception as e:
        logger.error(f"Error writing to CSV file {csv_file_path}: {e}")


# --- Main Execution ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Scrape Robert Half jobs and optionally analyze all."
    )
    parser.add_argument(
        "--analyze-all",
        action="store_true",  # Creates a boolean flag
        help="Force AI analysis on ALL jobs found in this run, not just new ones (for testing).",
    )
    parser.add_argument(
        "--llm-debug",
        action="store_true",
        help="Enable verbose debug logging for the LLM analysis steps.",
    )
    args = parser.parse_args()

    # Call the main function, passing the value of the flag
    scrape_roberthalf_jobs(analyze_all=args.analyze_all, llm_debug=args.llm_debug)
