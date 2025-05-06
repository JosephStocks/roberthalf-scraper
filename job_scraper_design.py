import abc
import csv
import json
import logging
import os
import random
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Literal, TypedDict
from urllib.parse import urlparse

import pytz
import requests
from playwright.sync_api import (
    Cookie as PlaywrightCookie,
    Error as PlaywrightError,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)


# Define types matching Playwright's expectations
class ProxySettingsRequired(TypedDict):
    server: str


class ProxySettings(ProxySettingsRequired, total=False):
    bypass: str
    username: str
    password: str


# Based on playwright.sync_api.Cookie
class SetCookieParamOptional(TypedDict, total=False):
    url: str
    domain: str
    path: str
    expires: float  # changed from datetime
    httpOnly: bool
    secure: bool
    sameSite: Literal["Lax", "None", "Strict"]


class SetCookieParam(SetCookieParamOptional):
    name: str
    value: str


# Config class for centralized configuration
class Config:
    """Centralized configuration management for job scrapers."""

    def __init__(self, env_file: str = ".env", override: bool = False):
        """Initialize configuration from environment file."""
        self.logger = logging.getLogger(__name__)
        self._config: dict[str, Any] = {}
        self._load_from_env(env_file, override)

    def _load_from_env(self, env_file: str, override: bool) -> None:
        """Load configuration from environment file."""
        from dotenv import load_dotenv

        env_path = Path(env_file)
        if env_path.exists():
            success = load_dotenv(dotenv_path=env_path, override=override)
            if success:
                self.logger.info(f"Loaded environment from {env_path}")
            else:
                self.logger.warning(f"Failed to load environment from {env_path}")
        else:
            self.logger.warning(f"Environment file {env_path} not found")

        # Load all needed environment variables
        self._load_core_config()
        self._load_proxy_config()
        self._load_job_sites_config()

    def _load_core_config(self) -> None:
        """Load core configuration variables."""
        # Path settings
        self._config["LOG_DIR"] = self._get_env("LOG_DIR", "logs")
        self._config["SESSION_DIR"] = self._get_env("SESSION_DIR", ".session")
        self._config["OUTPUT_DIR"] = self._get_env("OUTPUT_DIR", "output")
        self._config["DOCS_DIR"] = self._get_env("DOCS_DIR", "docs")

        # General settings
        self._config["LOG_LEVEL"] = self._get_env("LOG_LEVEL", "INFO")
        self._config["TEST_MODE"] = self._get_env_bool("TEST_MODE", False)
        self._config["SAVE_SESSION"] = self._get_env_bool("SAVE_SESSION", True)
        self._config["SESSION_FILE"] = self._get_env("SESSION_FILE", "session_data.json")
        self._config["SESSION_MAX_AGE_HOURS"] = self._get_env_int("SESSION_MAX_AGE_HOURS", 12)

        # Delay and timeout settings
        self._config["REQUEST_DELAY_SECONDS"] = self._get_env_float("REQUEST_DELAY_SECONDS", 2.0)
        self._config["PAGE_DELAY_MIN"] = self._get_env_float("PAGE_DELAY_MIN", 5.0)
        self._config["PAGE_DELAY_MAX"] = self._get_env_float("PAGE_DELAY_MAX", 15.0)
        self._config["MAX_RETRIES"] = self._get_env_int("MAX_RETRIES", 3)
        self._config["BROWSER_TIMEOUT_MS"] = self._get_env_int("BROWSER_TIMEOUT_MS", 60000)
        self._config["REQUEST_TIMEOUT_SECONDS"] = self._get_env_int("REQUEST_TIMEOUT_SECONDS", 30)

        # Browser settings
        self._config["HEADLESS_BROWSER"] = self._get_env_bool("HEADLESS_BROWSER", True)
        self._config["ROTATE_USER_AGENT"] = self._get_env_bool("ROTATE_USER_AGENT", False)
        self._config["DEFAULT_USER_AGENT"] = self._get_env(
            "DEFAULT_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
        )

        # Optional GitHub settings
        self._config["GITHUB_ACCESS_TOKEN"] = self._get_env("GITHUB_ACCESS_TOKEN", "")
        self._config["GITHUB_PAGES_URL"] = self._get_env("GITHUB_PAGES_URL", "")

        # Notification settings
        self._config["PUSHOVER_ENABLED"] = self._get_env_bool("PUSHOVER_ENABLED", False)
        self._config["PUSHOVER_API_TOKEN"] = self._get_env("PUSHOVER_API_TOKEN", "")
        self._config["PUSHOVER_USER_KEY_JOE"] = self._get_env("PUSHOVER_USER_KEY_JOE", "")
        self._config["PUSHOVER_USER_KEY_KATIE"] = self._get_env("PUSHOVER_USER_KEY_KATIE", "")

        # Scraper enable/disable flags
        self._config["ROBERTHALF_ENABLED"] = self._get_env_bool("ROBERTHALF_ENABLED", True)
        self._config["KDP_ENABLED"] = self._get_env_bool("KDP_ENABLED", False)

    def _load_proxy_config(self) -> None:
        """Load proxy configuration."""
        self._config["USE_PROXY"] = self._get_env_bool("USE_PROXY", False)
        if self._config["USE_PROXY"]:
            self._config["PROXY_SERVER"] = self._get_env("PROXY_SERVER", "")
            self._config["PROXY_AUTH"] = self._get_env("PROXY_AUTH", "")
            self._config["PROXY_BYPASS"] = self._get_env("PROXY_BYPASS", "")

            if not self._config["PROXY_SERVER"]:
                self.logger.warning("USE_PROXY is True but PROXY_SERVER is not set")

    def _load_job_sites_config(self) -> None:
        """Load job site specific configurations."""
        # Robert Half specific settings
        self._config["ROBERTHALF_USERNAME"] = self._get_env("ROBERTHALF_USERNAME", "")
        self._config["ROBERTHALF_PASSWORD"] = self._get_env("ROBERTHALF_PASSWORD", "")
        self._config["FILTER_STATE"] = self._get_env("FILTER_STATE", "TX")
        self._config["JOB_POST_PERIOD"] = self._get_env("JOB_POST_PERIOD", "PAST_24_HOURS")

        # Keurig Dr Pepper specific settings
        self._config["KDP_USERNAME"] = self._get_env("KDP_USERNAME", "")
        self._config["KDP_PASSWORD"] = self._get_env("KDP_PASSWORD", "")
        self._config["KDP_LOCATION"] = self._get_env("KDP_LOCATION", "")
        self._config["KDP_CATEGORY"] = self._get_env("KDP_CATEGORY", "")  # Category filter (e.g., "Supply Chain", "Sales")
        self._config["KDP_JOB_LEVEL"] = self._get_env("KDP_JOB_LEVEL", "")  # Job Level filter (e.g., "Manager", "Individual Contributor")

        # AI matching configuration (for any job site)
        self._config["MATCHING_ENABLED"] = self._get_env_bool("MATCHING_ENABLED", False)
        if self._config["MATCHING_ENABLED"]:
            self._config["OPENAI_API_KEY"] = self._get_env("OPENAI_API_KEY", "")
            self._config["CANDIDATE_PROFILE_PATH"] = self._get_env(
                "CANDIDATE_PROFILE_PATH", "candidate_profile.json"
            )
            self._config["MATCHING_MODEL_TIER1"] = self._get_env(
                "MATCHING_MODEL_TIER1", "gpt-4o-mini"
            )
            self._config["MATCHING_THRESHOLD_TIER1"] = self._get_env_int(
                "MATCHING_THRESHOLD_TIER1", 60
            )
            self._config["MATCHING_MODEL_TIER2"] = self._get_env(
                "MATCHING_MODEL_TIER2", "gpt-4o-mini"
            )
            self._config["MATCHING_THRESHOLD_FINAL"] = self._get_env_int(
                "MATCHING_THRESHOLD_FINAL", 75
            )

            if not self._config["OPENAI_API_KEY"]:
                self.logger.warning("MATCHING_ENABLED is True but OPENAI_API_KEY is not set")

    def _get_env(self, key: str, default: Any = None) -> Any:
        """Get environment variable with default fallback."""
        value = os.getenv(key)
        if value is None:
            return default

        # Clean the value (remove comments and whitespace)
        cleaned_value = value.split("#")[0].strip()
        if not cleaned_value:
            return default

        return cleaned_value

    def _get_env_bool(self, key: str, default: bool = False) -> bool:
        """Get boolean environment variable."""
        value = self._get_env(key)
        if value is None:
            return default

        return value.lower() in ("true", "yes", "y", "1", "t")

    def _get_env_int(self, key: str, default: int = 0) -> int:
        """Get integer environment variable."""
        value = self._get_env(key)
        if value is None:
            return default

        try:
            return int(value)
        except ValueError:
            self.logger.warning(
                f"Invalid integer value for {key}: {value}. Using default: {default}"
            )
            return default

    def _get_env_float(self, key: str, default: float = 0.0) -> float:
        """Get float environment variable."""
        value = self._get_env(key)
        if value is None:
            return default

        try:
            return float(value)
        except ValueError:
            self.logger.warning(f"Invalid float value for {key}: {value}. Using default: {default}")
            return default

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value with default fallback."""
        return self._config.get(key, default)

    def dump(self, exclude_sensitive: bool = True) -> dict[str, Any]:
        """Dump configuration as dictionary, optionally excluding sensitive fields."""
        if not exclude_sensitive:
            return self._config.copy()

        # Create a copy excluding sensitive fields
        result = self._config.copy()
        sensitive_keys = [
            "ROBERTHALF_PASSWORD",
            "KDP_PASSWORD",
            "OPENAI_API_KEY",
            "PUSHOVER_API_TOKEN",
            "PUSHOVER_USER_KEY_JOE",
            "PUSHOVER_USER_KEY_KATIE",
            "PROXY_AUTH",
            "GITHUB_ACCESS_TOKEN",
        ]

        for key in sensitive_keys:
            if result.get(key):
                result[key] = "********"

        return result


# --- Re-added class definitions ---
class JobStatus(Enum):
    NEW = "new"
    EXISTING = "existing"
    UPDATED = "updated"

@dataclass
class Job:
    """Base class for job data from any source."""

    job_id: str
    title: str
    company: str
    location: str
    date_posted: str
    url: str
    description: str = ""
    salary: str = "Not specified"
    job_type: str = "Not specified"
    status: JobStatus = JobStatus.NEW

    # Additional fields for matching and analysis
    is_new: bool = True
    is_remote: bool = False
    match_analysis: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert job to dictionary for serialization."""
        result = {
            "job_id": self.job_id,
            "title": self.title,
            "company": self.company,
            "location": self.location,
            "date_posted": self.date_posted,
            "url": self.url,
            "description": self.description,
            "salary": self.salary,
            "job_type": self.job_type,
            "status": self.status.value,
            "is_new": self.is_new,
            "is_remote": self.is_remote,
        }

        # Only include match_analysis if it exists
        if self.match_analysis:
            result["match_analysis"] = self.match_analysis

        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Job":
        """Create job from dictionary."""
        # Convert status string to enum
        status_str = data.pop("status", "new")
        status = JobStatus(status_str)

        # Extract match analysis if present
        match_analysis = data.pop("match_analysis", None)

        job = cls(**data, status=status)
        job.match_analysis = match_analysis
        return job

@dataclass
class RobertHalfJob(Job):
    """Robert Half specific job data."""

    state_province: str = ""
    pay_rate_min: str = ""
    pay_rate_max: str = ""
    pay_rate_period: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert Robert Half job to dictionary."""
        base_dict = super().to_dict()
        base_dict.update(
            {
                "state_province": self.state_province,
                "pay_rate_min": self.pay_rate_min,
                "pay_rate_max": self.pay_rate_max,
                "pay_rate_period": self.pay_rate_period,
            }
        )
        return base_dict

@dataclass
class KeurigDrPepperJob(Job):
    """Keurig Dr Pepper specific job data."""

    job_category: str = ""
    job_level: str = ""
    position_type: str = ""
    requirements: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert KDP job to dictionary."""
        base_dict = super().to_dict()
        base_dict.update(
            {
                "job_category": self.job_category,
                "job_level": self.job_level,
                "position_type": self.position_type,
                "requirements": self.requirements,
            }
        )
        return base_dict

# Abstract base class for job scrapers
class JobScraper(abc.ABC):
    """Abstract base class for job scrapers."""

    def __init__(self, config: Config, llm_debug: bool = False):
        """Initialize the job scraper.

        Args:
            config: Configuration manager
            llm_debug: Enable debug logging for LLM operations
        """
        self.config = config
        self.llm_debug = llm_debug
        self.logger = logging.getLogger(self.__class__.__name__)

        # Initialize directories
        self.log_dir = Path(config.get("LOG_DIR", "logs"))
        self.session_dir = Path(config.get("SESSION_DIR", ".session"))
        self.output_dir = Path(config.get("OUTPUT_DIR", "output"))
        self.docs_dir = Path(config.get("DOCS_DIR", "docs"))

        # Create directories
        self._create_directories()

        # Session management
        self.save_session = config.get("SAVE_SESSION", True)
        self.session_max_age_hours = config.get("SESSION_MAX_AGE_HOURS", 12)
        self.session_file_path = self.session_dir / self._get_session_filename()

        # Browser settings
        self.headless = config.get("HEADLESS_BROWSER", True)
        self.rotate_user_agent = config.get("ROTATE_USER_AGENT", False)
        self.default_user_agent = config.get("DEFAULT_USER_AGENT", "Mozilla/5.0 (...)")
        self.browser_timeout_ms = config.get("BROWSER_TIMEOUT_MS", 60000)

        # Request settings
        self.request_delay = config.get("REQUEST_DELAY_SECONDS", 2.0)
        self.page_delay_min = config.get("PAGE_DELAY_MIN", 5.0)
        self.page_delay_max = config.get("PAGE_DELAY_MAX", 15.0)
        self.max_retries = config.get("MAX_RETRIES", 3)
        self.request_timeout = config.get("REQUEST_TIMEOUT_SECONDS", 30)

        # State tracking
        self.session_cookies: Sequence[SetCookieParam] = []
        self.session_user_agent: str = ""
        self.jobs_found: Sequence[Job] = [] # Use Sequence[Job] for covariance
        self.existing_job_ids: set[str] = set()
        self.new_job_ids: set[str] = set()

    def _create_directories(self) -> None:
        """Create necessary directories if they don't exist."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.docs_dir.mkdir(parents=True, exist_ok=True)

    @abc.abstractmethod
    def _get_session_filename(self) -> str:
        """Get the session filename for this scraper."""
        pass

    def get_user_agent(self) -> str:
        """Get a user agent string based on configuration."""
        if not self.rotate_user_agent:
            return self.default_user_agent

        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/133.0.2782.0",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
        ]
        return random.choice(user_agents)

    def add_human_delay(self, min_seconds: float = 0.5, max_seconds: float = 1.5) -> None:
        """Add a random delay to simulate human interaction."""
        delay = random.uniform(min_seconds, max_seconds)
        self.logger.debug(f"Adding delay of {delay:.2f} seconds")
        time.sleep(delay)

    @abc.abstractmethod
    def login_and_get_session(self) -> tuple[list[SetCookieParam], str] | None:
        """Log in to the job site and get session cookies."""
        pass

    @abc.abstractmethod
    def validate_session(self, cookies: Sequence[SetCookieParam], user_agent: str) -> bool:
        """Validate session cookies."""
        pass

    @abc.abstractmethod
    def load_session_data(self) -> tuple[list[SetCookieParam], str] | None:
        """Load session data from disk.

        Returns:
            Tuple of cookies and user agent, or None if no valid session found
        """
        pass

    @abc.abstractmethod
    def save_session_data(self, cookies: Sequence[SetCookieParam], user_agent: str) -> None:
        """Save session data to disk.

        Args:
            cookies: Session cookies to save
            user_agent: User agent string to save
        """
        pass

    def get_or_refresh_session(self) -> tuple[Sequence[SetCookieParam], str] | None:
        """Get existing session or create a new one if needed."""
        loaded_data = self.load_session_data()
        if loaded_data:
            self.logger.info("Found existing session data.")
            self.session_cookies, self.session_user_agent = loaded_data
            return loaded_data

        self.logger.info("No valid session found. Performing new login.")
        login_result = self.login_and_get_session()
        if login_result:
            self.session_cookies, self.session_user_agent = login_result
            self.save_session_data(self.session_cookies, self.session_user_agent)
            return login_result

        self.logger.error("Failed to obtain a session after login attempt.")
        return None

    def get_proxy_config(self) -> ProxySettings | None:
        """Get proxy configuration if enabled.

        Returns:
            ProxySettings dict or None if proxies are disabled
        """
        if not self.config.get("USE_PROXY", False):
            return None

        proxy_server = self.config.get("PROXY_SERVER", "")
        if not proxy_server:
            self.logger.warning("USE_PROXY is True but PROXY_SERVER is not set")
            return None

        proxy_settings: ProxySettings = {"server": proxy_server}

        # Add optional proxy settings if available
        if bypass := self.config.get("PROXY_BYPASS", ""):
            proxy_settings["bypass"] = bypass

        if (auth := self.config.get("PROXY_AUTH", "")) and ":" in auth:
            username, password = auth.split(":", 1)
            proxy_settings["username"] = username
            proxy_settings["password"] = password

        return proxy_settings

    @abc.abstractmethod
    def scrape_jobs(self, analyze_all: bool = False) -> Sequence[Job]: # Return Sequence[Job]
        """Main method to scrape jobs."""
        pass

    @abc.abstractmethod
    def generate_report(self, jobs: Sequence[Job], report_file: Path) -> None: # Type jobs as Sequence[Job] here, subclasses specify more
        """Generate an HTML report from job data."""
        pass

    @abc.abstractmethod
    def load_existing_job_ids(self) -> set[str]:
        """Load existing job IDs from previous runs."""
        pass


# --- End Re-added/Moved definitions ---


# Robert Half Implementation
class RobertHalfScraper(JobScraper):
    """Robert Half job scraper implementation."""

    def __init__(self, config: Config, llm_debug: bool = False):
        """Initialize the Robert Half scraper."""
        super().__init__(config, llm_debug)

        # Robert Half specific settings
        self.filter_state = config.get("FILTER_STATE", "TX")
        self.job_post_period = config.get("JOB_POST_PERIOD", "PAST_24_HOURS")
        self.username = config.get("ROBERTHALF_USERNAME", "")
        self.password = config.get("ROBERTHALF_PASSWORD", "")

        # Validation
        if not self.username or not self.password:
            self.logger.warning("Robert Half credentials not found in config.")

        # CSV path for tracking jobs
        self.csv_file_path = self.output_dir / "roberthalf_job_data.csv"

    def _get_session_filename(self) -> str:
        """Get the session filename for Robert Half."""
        return "roberthalf_session.json"

    def load_session_data(self) -> tuple[list[SetCookieParam], str] | None:
        """Load Robert Half session data from disk."""
        if not self.session_file_path.exists():
            return None

        try:
            # Check if file is too old
            file_age = datetime.now(UTC) - datetime.fromtimestamp(
                self.session_file_path.stat().st_mtime, tz=UTC
            )
            if file_age > timedelta(hours=self.session_max_age_hours):
                self.logger.info(
                    f"Session file is {file_age.total_seconds() / 3600:.1f} hours old (max {self.session_max_age_hours}). Requiring refresh."
                )
                return None

            # Load and parse the file
            with open(self.session_file_path, encoding="utf-8") as f:
                data = json.load(f)

            # Check for required keys
            if "cookies" not in data or "user_agent" not in data:
                self.logger.warning("Session file is missing required keys.")
                return None

            # Validate session
            cookies: list[SetCookieParam] = data["cookies"]
            user_agent: str = data["user_agent"]

            if not self.validate_session(cookies, user_agent):
                self.logger.info("Loaded session failed validation.")
                return None

            self.logger.info(f"Loaded valid session from {self.session_file_path}")
            return cookies, user_agent

        except json.JSONDecodeError:
            self.logger.warning(f"Session file {self.session_file_path} contains invalid JSON.")
            return None
        except Exception as e:
            self.logger.warning(f"Error loading session from {self.session_file_path}: {e}")
            return None

    def save_session_data(self, cookies: Sequence[SetCookieParam], user_agent: str) -> None:
        """Save Robert Half session data to disk."""
        if not self.save_session:
            self.logger.debug("Session saving disabled. Skipping.")
            return

        try:
            data = {
                "cookies": list(cookies),  # Convert sequence to list
                "user_agent": user_agent,
                "timestamp": datetime.now(UTC).isoformat(),
            }

            with open(self.session_file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            self.logger.info(f"Saved session data to {self.session_file_path}")

        except Exception as e:
            self.logger.error(f"Error saving session to {self.session_file_path}: {e}")

    def login_and_get_session(self) -> tuple[list[SetCookieParam], str] | None:
        """Log in to Robert Half and get session cookies."""
        self.logger.info("Starting login process with Playwright")
        session_user_agent = self.get_user_agent()
        self.logger.info(f"Using User Agent: {session_user_agent}")

        # Validate credentials
        if not self.username or not self.password:
            self.logger.error("Robert Half credentials not found.")
            return None

        browser = None
        context = None
        page: Page | None = None # Type hint for page

        with sync_playwright() as p:
            proxy_config = self.get_proxy_config()
            try:
                browser = p.chromium.launch(
                    proxy=proxy_config, headless=self.headless, timeout=self.browser_timeout_ms  # type: ignore[arg-type]
                )

                context = browser.new_context(
                    # proxy=proxy_config, # Proxy should be set at launch, not context? Check Playwright docs. Let's remove it here for now.
                    viewport={"width": 1920, "height": 1080},
                    user_agent=session_user_agent,
                    java_script_enabled=True,
                    accept_downloads=False,
                    ignore_https_errors=True,
                )

                context.set_default_navigation_timeout(self.browser_timeout_ms)
                page = context.new_page()

                # Navigate to login page
                login_url = "https://online.roberthalf.com/s/login?app=0sp3w000001UJH5&c=US&d=en_US&language=en_US&redirect=false"
                self.logger.info(f"Navigating to login page: {login_url}")
                page.goto(login_url, wait_until="domcontentloaded", timeout=self.browser_timeout_ms)
                self.add_human_delay(2, 4)

                # Fill username
                username_field = page.locator('[data-id="username"] input')
                username_field.wait_for(state="visible", timeout=15000)
                username_field.fill(self.username)
                self.add_human_delay()

                # Fill password
                password_field = page.locator('[data-id="password"] input')
                password_field.wait_for(state="visible", timeout=10000)
                password_field.fill(self.password)
                self.add_human_delay()

                # Click sign in
                sign_in_button = page.locator('rhcl-button[data-id="signIn"]')
                sign_in_button.click()

                # Wait for post-login redirect
                try:
                    page.wait_for_url("**/s/myjobs", timeout=self.browser_timeout_ms / 2)
                    self.logger.info("Post-login URL reached.")
                except PlaywrightTimeoutError:
                    # Check for error messages
                    error_locator = page.locator('div[role="alert"]:visible, .login-error:visible')
                    if error_locator.is_visible(timeout=2000):
                        error_text = (
                            error_locator.first.text_content(timeout=1000)
                            or "[Could not get error text]"
                        )
                        self.logger.error(f"Login failed: {error_text.strip()}")
                        if page:
                            page.screenshot(path=self.output_dir / "playwright_login_error.png")
                        return None
                    else:
                        self.logger.warning(
                            "Timeout waiting for post-login confirmation, proceeding cautiously."
                        )
                except Exception as wait_err:
                    self.logger.error(f"Error during post-login wait: {wait_err}")
                    return None

                # Get cookies
                playwright_cookies: list[PlaywrightCookie] = context.cookies()
                if not playwright_cookies:
                    self.logger.error("Failed to retrieve cookies after login attempt.")
                    return None

                # Convert cookies to SetCookieParam format
                cookies: list[SetCookieParam] = []
                for pc in playwright_cookies:
                    # Use .get() for potentially missing keys from PlaywrightCookie TypedDict
                    cookie: SetCookieParam = {
                        "name": pc.get("name", ""),
                        "value": pc.get("value", ""),
                        "domain": pc.get("domain", ""),
                        "path": pc.get("path", ""),
                    }
                    if not cookie["name"] or not cookie["value"]:
                        self.logger.warning(f"Skipping cookie with missing name/value: {pc}")
                        continue

                    # Optional fields
                    if pc.get("expires", -1) != -1:  # -1 means session cookie
                        expires = pc.get("expires")
                        if expires is not None:  # Explicit check for None before assignment
                            cookie["expires"] = expires
                    if "httpOnly" in pc:
                        cookie["httpOnly"] = pc["httpOnly"]
                    if "secure" in pc:
                        cookie["secure"] = pc["secure"]
                    if "sameSite" in pc and pc["sameSite"] in ["Lax", "None", "Strict"]:
                        cookie["sameSite"] = pc["sameSite"]
                    cookies.append(cookie)

                self.logger.info(f"Login successful, {len(cookies)} cookies obtained.")
                return cookies, session_user_agent

            except PlaywrightTimeoutError as te:
                self.logger.error(f"Timeout during Playwright operation: {te}")
                if page:
                    page.screenshot(path=self.output_dir / "playwright_timeout_error.png")
                return None

            except PlaywrightError as pe:
                self.logger.error(f"Playwright error during login: {pe}")
                return None

            except Exception as e:
                self.logger.error(f"Unexpected error during login: {e}", exc_info=True)
                return None

            finally:
                if context:
                    context.close()
                if browser:
                    browser.close()

    def validate_session(self, cookies: Sequence[SetCookieParam], user_agent: str) -> bool:
        """Validate Robert Half session cookies."""
        self.logger.info("Validating session cookies via API")
        url = "https://www.roberthalf.com/bin/jobSearchServlet"

        # Convert SetCookieParam sequence to simple dict for requests
        cookie_dict = {cookie["name"]: cookie["value"] for cookie in cookies}

        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "en-US,en;q=0.9",
            "content-type": "application/json",
            "origin": "https://www.roberthalf.com",
            "referer": "https://www.roberthalf.com/us/en/jobs",
            "user-agent": user_agent,
        }

        # Minimal payload
        payload = {
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
                url,
                headers=headers,
                cookies=cookie_dict,
                json=payload,
                timeout=self.request_timeout,
            )

            if 200 <= response.status_code < 300:
                try:
                    response.json()  # Check if response is valid JSON
                    self.logger.info("Session validation successful (API responded with JSON)")
                    return True
                except json.JSONDecodeError:
                    self.logger.warning(
                        f"Session validation failed: Status {response.status_code} but response was not JSON."
                    )
                    return False
            else:
                self.logger.warning(
                    f"Session validation failed: Status code {response.status_code}"
                )
                return False

        except requests.exceptions.RequestException as e:
            self.logger.warning(f"Session validation failed due to network error: {e}")
            return False

    def fetch_jobs(
        self,
        cookies: Sequence[SetCookieParam],
        user_agent: str,
        page_number: int = 1,
        is_remote: bool = False,
    ) -> dict[str, Any] | None:
        """Fetch jobs from Robert Half API."""
        url = "https://www.roberthalf.com/bin/jobSearchServlet"

        # Convert SetCookieParam sequence to simple dict for requests
        cookie_dict = {cookie["name"]: cookie["value"] for cookie in cookies}

        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "en-US,en;q=0.9",
            "content-type": "application/json",
            "origin": "https://www.roberthalf.com",
            "referer": "https://www.roberthalf.com/us/en/jobs",
            "user-agent": user_agent,
        }

        # Full search payload
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
            "postedwithin": self.job_post_period,
            "timetype": "",
            "pagesize": 25,
            "pagenumber": page_number,
            "sortby": "PUBLISHED_DATE_DESC",
            "mode": "",
            "payratemin": 0,
            "includedoe": "",
        }

        # Set up proxy if configured
        proxies = None
        proxy_config = self.get_proxy_config() # Returns ProxySettings | None
        if proxy_config:
            server_url = proxy_config.get("server")
            username = proxy_config.get("username")
            password = proxy_config.get("password")
            # Note: Playwright ProxySettings may include scheme, requests needs http/https schemes in the proxies dict
            if server_url:
                # Basic check if scheme is suitable for requests
                parsed_url = urlparse(server_url)
                scheme = parsed_url.scheme
                if scheme and scheme.startswith("http"):
                    proxy_url = server_url
                    if username and password:
                        auth = f"{username}:{password}"
                        # Rebuild URL with auth for requests
                        proxy_url = f"{scheme}://{auth}@{parsed_url.netloc}{parsed_url.path}"

                    proxies = {"http": proxy_url, "https": proxy_url}
                    self.logger.debug(f"Using proxy for requests: {scheme}://{parsed_url.netloc}")
                elif scheme and scheme.startswith("sock"):
                     # Requests needs requests[socks] extra for socks proxies
                     self.logger.warning(
                        f"SOCKS proxy configured ({server_url}), but requests needs 'requests[socks]' installed to use it."
                     )
                     # Set proxies anyway, it will fail if socks extra is not installed
                     proxy_url = server_url
                     if username and password:
                          auth = f"{username}:{password}"
                          proxy_url = f"{scheme}://{auth}@{parsed_url.netloc}{parsed_url.path}"
                     proxies = {"http": proxy_url, "https": proxy_url}
                else:
                     self.logger.warning(
                        f"Proxy server '{server_url}' has unsupported scheme for requests: {scheme}"
                     )

        response = None
        try:
            self.logger.info(
                f"Fetching {'remote' if is_remote else 'local'} jobs page {page_number}"
            )
            response = requests.post(
                url,
                headers=headers,
                cookies=cookie_dict,
                json=payload,
                timeout=self.request_timeout,
                proxies=proxies,
            )

            response.raise_for_status()
            return response.json()

        except json.JSONDecodeError:
            status_code = response.status_code if response is not None else "N/A"
            response_text = response.text[:200] if response is not None else "N/A"
            self.logger.warning(
                f"Failed to parse API response as JSON (Status: {status_code}). Body: {response_text}..."
            )
            return None

        except requests.exceptions.HTTPError as http_err:
            status_code = http_err.response.status_code
            if status_code in (401, 403):
                self.logger.warning(f"HTTP {status_code} error suggests session is invalid.")
            else:
                self.logger.error(f"HTTP error fetching jobs page {page_number}: {http_err}")
            return None

        except requests.exceptions.RequestException as req_err:
            self.logger.error(f"Network error fetching jobs page {page_number}: {req_err}")
            return None

        except Exception as e:
            self.logger.error(
                f"Unexpected error fetching jobs page {page_number}: {e}", exc_info=True
            )
            return None

    def fetch_with_retry(
        self,
        cookies: Sequence[SetCookieParam],
        user_agent: str,
        page_number: int,
        is_remote: bool = False,
    ) -> dict[str, Any] | None:
        """Fetch jobs with retry logic."""
        base_wait_time = 5
        for attempt in range(self.max_retries):
            result = self.fetch_jobs(cookies, user_agent, page_number, is_remote)
            if result is not None:
                # Check if result indicates session invalidity (e.g., based on content or specific error code if API provides one)
                # If session is invalid, potentially break retry loop early or re-authenticate
                # For now, we just return the result. Validation happens separately.
                return result

            # Exponential backoff with jitter
            wait_time = base_wait_time * (2**attempt) + random.uniform(0, base_wait_time / 2)
            self.logger.warning(
                f"API fetch attempt {attempt + 1}/{self.max_retries} failed. Retrying in {wait_time:.2f}s..."
            )
            time.sleep(wait_time)

        self.logger.error(f"All {self.max_retries} retry attempts failed for page {page_number}.")
        return None

    def filter_jobs_by_state(
        self, jobs: list[dict[str, Any]], state_code: str
    ) -> list[dict[str, Any]]:
        """Filter jobs by state code or remote status."""
        filtered_jobs = [
            job
            for job in jobs
            if (job.get("stateprovince") == state_code)
            or (job.get("remote", "").lower() == "yes" and job.get("country", "").lower() == "us")
        ]

        return filtered_jobs

    def load_existing_job_ids(self) -> set[str]:
        """Load existing job IDs from CSV file."""
        existing_jobs = set()

        if not self.csv_file_path.exists():
            self.logger.info(f"CSV file {self.csv_file_path} not found. Starting fresh.")
            return existing_jobs

        try:
            with open(self.csv_file_path, newline="", encoding="utf-8") as csvfile:
                # Check if file is empty
                first_line = csvfile.readline()
                if not first_line:
                    return existing_jobs

                csvfile.seek(0)  # Reset to beginning

                reader = csv.DictReader(csvfile)
                if not reader.fieldnames or "Job ID" not in reader.fieldnames:
                    self.logger.error(
                        f"CSV file {self.csv_file_path} is missing required header ('Job ID')."
                    )
                    return existing_jobs

                for row in reader:
                    if job_id := row.get("Job ID"):
                        existing_jobs.add(job_id)

            self.logger.info(
                f"Read {len(existing_jobs)} existing job IDs from {self.csv_file_path}"
            )

        except Exception as e:
            self.logger.error(f"Error reading existing job data from {self.csv_file_path}: {e}")

        return existing_jobs

    def save_jobs_to_csv(self, jobs: list[RobertHalfJob]) -> None:
        """Save jobs to CSV file."""
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

        new_jobs_count = 0
        is_new_file = not self.csv_file_path.exists() or self.csv_file_path.stat().st_size == 0

        try:
            with open(self.csv_file_path, mode="a", newline="", encoding="utf-8") as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

                if is_new_file:
                    writer.writeheader()
                    self.logger.info(f"Created new CSV file: {self.csv_file_path}")

                for job in jobs:
                    if job.job_id and job.job_id not in self.existing_job_ids:
                        # Format pay rate
                        pay_rate = "N/A"
                        if job.pay_rate_min and job.pay_rate_max and job.pay_rate_period:
                            try:
                                pay_min = int(float(job.pay_rate_min))
                                pay_max = int(float(job.pay_rate_max))
                                pay_rate = f"${pay_min:,} - ${pay_max:,}/{job.pay_rate_period}"
                            except (ValueError, TypeError):
                                pay_rate = (
                                    f"{job.pay_rate_min}-{job.pay_rate_max}/{job.pay_rate_period}"
                                )

                        writer.writerow(
                            {
                                "Job ID": job.job_id,
                                "Job Title": job.title,
                                "Date First Seen (UTC)": datetime.now(UTC)
                                .isoformat(timespec="seconds")
                                .replace("+00:00", "Z"),
                                "Date Posted": job.date_posted,
                                "Location": job.location,
                                "Company Name": job.company,
                                "Pay Rate": pay_rate,
                                "Job URL": job.url,
                            }
                        )

                        new_jobs_count += 1
                        self.existing_job_ids.add(job.job_id)

            if new_jobs_count > 0:
                self.logger.info(f"Added {new_jobs_count} new job entries to {self.csv_file_path}")

        except Exception as e:
            self.logger.error(f"Error writing to CSV file {self.csv_file_path}: {e}")

    def convert_api_job_to_model(self, job_data: dict[str, Any], is_new: bool) -> RobertHalfJob:
        """Convert API job data to RobertHalfJob model."""
        # Extract location information
        city = job_data.get("city", "N/A")
        state = job_data.get("stateprovince", "")
        is_remote = job_data.get("remote", "").lower() == "yes"

        location = "Remote (US)" if is_remote else f"{city}, {state}"

        # Extract job details
        job_id = job_data.get("unique_job_number", "")
        title = job_data.get("jobtitle", "N/A")
        url = job_data.get("job_detail_url", "#")
        description = job_data.get("description", "")
        date_posted = job_data.get("date_posted", "")

        # Extract pay information
        pay_min = job_data.get("payrate_min", "")
        pay_max = job_data.get("payrate_max", "")
        pay_period = job_data.get("payrate_period", "")

        # Format salary string
        salary = "Not specified"
        if pay_min and pay_max and pay_period:
            try:
                pay_min_val = int(float(pay_min))
                pay_max_val = int(float(pay_max))
                salary = f"${pay_min_val:,} - ${pay_max_val:,} / {pay_period}"
            except (ValueError, TypeError):
                salary = f"{pay_min} - {pay_max} ({pay_period})"

        return RobertHalfJob(
            job_id=job_id,
            title=title,
            company="Robert Half",
            location=location,
            date_posted=date_posted,
            url=url,
            description=description,
            salary=salary,
            job_type=job_data.get("jobtype", "Not specified"),
            is_new=is_new,
            is_remote=is_remote,
            state_province=state,
            pay_rate_min=pay_min,
            pay_rate_max=pay_max,
            pay_rate_period=pay_period,
            status=JobStatus.NEW if is_new else JobStatus.EXISTING,
        )

    def scrape_jobs(self, analyze_all: bool = False) -> Sequence[RobertHalfJob]: # Use Sequence
        """Scrape jobs from Robert Half."""
        self.logger.info("--- Starting Robert Half Job Scraper ---")

        # Get session
        session_info = self.get_or_refresh_session()
        if not session_info:
            raise RuntimeError("Failed to establish a valid session.")

        cookies, user_agent = session_info
        self.existing_job_ids = self.load_existing_job_ids()

        all_filtered_jobs = []
        total_jobs_api_reported = 0

        # Fetch local and remote jobs
        for is_remote in [False, True]:
            page_number = 1
            jobs_found_this_type = None

            while True:
                job_type_str = "Remote" if is_remote else "Local"
                self.logger.info(f"--- Processing {job_type_str} Page {page_number} ---")

                response_data = self.fetch_with_retry(cookies, user_agent, page_number, is_remote)

                if not response_data:
                    self.logger.warning(f"Fetch failed for {job_type_str} page {page_number}.")
                    if not self.validate_session(cookies, user_agent):
                        raise RuntimeError("Session became invalid during pagination.")
                    else:
                        raise RuntimeError(
                            f"Failed to fetch {job_type_str} page {page_number} despite valid session."
                        )

                # Track total jobs count
                if jobs_found_this_type is None:
                    try:
                        current_found = int(response_data.get("found", 0))
                        jobs_found_this_type = current_found
                        total_jobs_api_reported += current_found
                        self.logger.info(
                            f"API reports {current_found} total {job_type_str} jobs for period '{self.job_post_period}'"
                        )
                    except (ValueError, TypeError):
                        self.logger.warning("Could not parse 'found' count.")
                        jobs_found_this_type = -1

                # Process jobs on this page
                jobs_on_page = response_data.get("jobs", [])
                if not jobs_on_page:
                    self.logger.info(f"No more {job_type_str} jobs on page {page_number}.")
                    break

                self.logger.info(
                    f"Received {len(jobs_on_page)} {job_type_str} jobs on page {page_number}."
                )

                # Filter jobs by state
                state_jobs_on_page = self.filter_jobs_by_state(jobs_on_page, self.filter_state)
                all_filtered_jobs.extend(state_jobs_on_page)

                # Check if this is the last page
                if len(jobs_on_page) < 25:  # Assuming page size is 25
                    self.logger.info("Received less than page size. Assuming last page.")
                    break

                if jobs_found_this_type >= 0:
                    max_pages_expected = (jobs_found_this_type + 24) // 25
                    if page_number >= max_pages_expected:
                        self.logger.info(
                            f"Reached expected max page number ({page_number}/{max_pages_expected})."
                        )
                        break

                # Next page
                page_number += 1
                page_delay = random.uniform(self.page_delay_min, self.page_delay_max)
                self.logger.debug(f"Waiting {page_delay:.2f}s before next page.")
                time.sleep(page_delay)

            # Add delay between local and remote fetching
            if not is_remote:
                switch_delay = random.uniform(self.page_delay_min * 1.2, self.page_delay_max * 1.2)
                self.logger.info(
                    f"Finished local. Switching to remote. Waiting {switch_delay:.2f}s..."
                )
                time.sleep(switch_delay)

        # Deduplicate jobs
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
                self.logger.warning("Job found without unique_job_number.")

        unique_job_list = list(unique_jobs_dict.values())
        self.logger.info(
            f"Total unique jobs found: {len(unique_job_list)} (Removed {duplicates_found} duplicates)."
        )

        # Identify new jobs
        self.existing_job_ids = self.load_existing_job_ids() # Load fresh before comparison
        unique_job_ids = {
            job.get("unique_job_number") for job in unique_job_list if job.get("unique_job_number")
        }
        new_job_ids = unique_job_ids - self.existing_job_ids

        self.logger.info(f"Identified {len(new_job_ids)} new jobs compared to CSV history.")
        self.new_job_ids = new_job_ids

        # Convert to model objects
        robert_half_jobs: list[RobertHalfJob] = [] # Ensure type
        for job_data in unique_job_list:
            job_id = job_data.get("unique_job_number", "")
            is_new = job_id in new_job_ids
            robert_half_job = self.convert_api_job_to_model(job_data, is_new)
            robert_half_jobs.append(robert_half_job)

        # Save to CSV
        self.save_jobs_to_csv(robert_half_jobs)

        # Sort by new > date posted > title
        robert_half_jobs.sort(
            key=lambda x: (x.is_new, x.date_posted, x.title),
            reverse=True,
        )

        self.jobs_found = robert_half_jobs # Store the final list (assignment is covariant)
        self.logger.info(f"--- Robert Half Scraper finished. Found {len(self.jobs_found)} jobs. ---")
        return robert_half_jobs # Return the specific list

    def generate_report(self, jobs: Sequence[Job], report_file: Path) -> None:
        """Generate an HTML report from job data."""
        # Type check that all jobs are RobertHalfJob instances
        robert_half_jobs = [job for job in jobs if isinstance(job, RobertHalfJob)]
        if len(robert_half_jobs) != len(jobs):
            self.logger.warning(f"Expected all jobs to be RobertHalfJob instances, but found {len(jobs) - len(robert_half_jobs)} other types")

        timestamp_dt = datetime.now(UTC)
        # timestamp_str = timestamp_dt.strftime("%Y%m%d_%H%M%S") # F841: Unused
        # iso_timestamp_str = timestamp_dt.isoformat(timespec="seconds").replace("+00:00", "Z") # F841: Unused

        # Convert UTC timestamp to local time (America/Chicago)
        cst = pytz.timezone("America/Chicago")
        dt_cst = timestamp_dt.astimezone(cst)
        formatted_timestamp = dt_cst.strftime("%Y-%m-%d %H:%M:%S %Z")

        # Count jobs by type
        tx_jobs = [job for job in robert_half_jobs if job.state_province == self.filter_state]
        remote_jobs = [job for job in robert_half_jobs if job.is_remote]
        new_jobs_count = sum(1 for job in robert_half_jobs if job.is_new)

        # HTML generation
        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Robert Half Job Report ({self.filter_state}) - {formatted_timestamp}</title>
    <style>
        body {{ font-family: sans-serif; margin: 20px; }}
        h1, h2 {{ color: #333; }}
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
        .salary {{ white-space: nowrap; }}
        .job-id {{ font-family: monospace; font-size: 0.9em; color: #666; }}

        /* Styling for the expandable content */
        .job-row {{
            cursor: pointer;
        }}
        .job-row:hover {{
            background-color: #f0f8ff;
        }}
        .job-description {{
            padding: 15px;
            background-color: #fff;
            border-top: none;
            margin-top: 0;
            max-height: 300px; /* Limit description height */
            overflow-y: auto; /* Add scrollbar if needed */
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
            background-color: #f2f2f2;
            font-weight: bold;
            font-size: 14px;
        }}

        /* Styling for new job highlighting */
        .new-job .title-cell {{
            background-color: #f0fff0; /* Light green background for new jobs */
        }}
        .new-tag {{
            display: inline-block;
            background-color: #28a745;
            color: white;
            padding: 2px 6px;
            font-size: 0.75em;
            font-weight: bold;
            border-radius: 4px;
            margin-right: 5px;
            vertical-align: middle;
        }}
    </style>
</head>
<body>
    <h1>Robert Half Job Report</h1>
    <p>Generated: {formatted_timestamp}</p>
    <p>State Filter: {self.filter_state} | Post Period: {self.job_post_period}</p>
    <p>Found {len(robert_half_jobs)} jobs ({new_jobs_count} new).</p>
    <p>State ({self.filter_state}): {len(tx_jobs)} jobs | Remote (US): {len(remote_jobs)} jobs</p>

    <h2>Jobs Found</h2>
    <table id="jobTable">
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

        # Add job rows
        if not robert_half_jobs:
            html_content += '<tr><td colspan="5">No jobs found matching the criteria.</td></tr>'
        else:
            for idx, job in enumerate(robert_half_jobs, 1):
                new_indicator_html = '<span class="new-tag">NEW</span> ' if job.is_new else ""
                row_class = "job-row new-job" if job.is_new else "job-row"

                html_content += f"""
                <tr class="{row_class}" data-job-id="{idx}">
                    <td class="title-cell"><span class="expander">+</span> {new_indicator_html}<a href="{job.url}" target="_blank">{job.title}</a></td>
                    <td>{job.location}</td>
                    <td class="salary">{job.salary}</td>
                    <td class="job-id">{job.job_id}</td>
                    <td>{job.date_posted}</td>
                </tr>
                <tr class="description-row" id="job-{idx}" style="display:none;">
                    <td colspan="5" class="description-container">
                        <div class="job-description">
                            {job.description if job.description else "<p>No description provided.</p>"}
                        </div>
                    </td>
                </tr>
"""

        # Add footer and JavaScript
        html_content += """
        </tbody>
    </table>
    <script>
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

        # Write the HTML report
        try:
            with open(report_file, "w", encoding="utf-8") as f:
                f.write(html_content)

            self.logger.info(f"Generated HTML report at: {report_file.resolve()}")

        except Exception as e:
            self.logger.error(f"Failed to generate/save HTML report: {e}")


# Keurig Dr Pepper Implementation
class KeurigDrPepperScraper(JobScraper):
    """Keurig Dr Pepper job scraper implementation."""

    def __init__(self, config: Config, llm_debug: bool = False):
        """Initialize the KDP scraper."""
        super().__init__(config, llm_debug)

        # KDP specific settings
        self.username = config.get("KDP_USERNAME", "")
        self.password = config.get("KDP_PASSWORD", "")
        self.location_filter = config.get("KDP_LOCATION", "")
        self.category_filter = config.get("KDP_CATEGORY", "")  # Category filter (e.g., "Supply Chain", "Sales")
        self.job_level_filter = config.get("KDP_JOB_LEVEL", "")  # Job Level filter (e.g., "Manager", "Individual Contributor")

        # KDP specific constants
        self.base_url = "https://careers.keurigdrpepper.com"
        self.search_url = f"{self.base_url}/en/search-jobs"

        # CSV path for tracking jobs
        self.csv_file_path = self.output_dir / "kdp_job_data.csv"

    def _get_session_filename(self) -> str:
        """Get the session filename for KDP."""
        return "kdp_session.json"

    def load_session_data(self) -> tuple[list[SetCookieParam], str] | None:
        """Load KDP session data from disk."""
        if not self.session_file_path.exists():
            return None

        try:
            # Check if file is too old
            file_age = datetime.now(UTC) - datetime.fromtimestamp(
                self.session_file_path.stat().st_mtime, tz=UTC
            )
            if file_age > timedelta(hours=self.session_max_age_hours):
                self.logger.info(
                    f"Session file is {file_age.total_seconds() / 3600:.1f} hours old (max {self.session_max_age_hours}). Requiring refresh."
                )
                return None

            # Load and parse the file
            with open(self.session_file_path, encoding="utf-8") as f:
                data = json.load(f)

            # Check for required keys
            if "cookies" not in data or "user_agent" not in data:
                self.logger.warning("Session file is missing required keys.")
                return None

            # Validate session
            cookies: list[SetCookieParam] = data["cookies"]
            user_agent: str = data["user_agent"]

            if not self.validate_session(cookies, user_agent):
                self.logger.info("Loaded session failed validation.")
                return None

            self.logger.info(f"Loaded valid session from {self.session_file_path}")
            return cookies, user_agent

        except json.JSONDecodeError:
            self.logger.warning(f"Session file {self.session_file_path} contains invalid JSON.")
            return None
        except Exception as e:
            self.logger.warning(f"Error loading session from {self.session_file_path}: {e}")
            return None

    def save_session_data(self, cookies: Sequence[SetCookieParam], user_agent: str) -> None:
        """Save KDP session data to disk."""
        if not self.save_session:
            self.logger.debug("Session saving disabled. Skipping.")
            return

        try:
            data = {
                "cookies": list(cookies),  # Convert sequence to list
                "user_agent": user_agent,
                "timestamp": datetime.now(UTC).isoformat(),
            }

            with open(self.session_file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            self.logger.info(f"Saved session data to {self.session_file_path}")

        except Exception as e:
            self.logger.error(f"Error saving session to {self.session_file_path}: {e}")

    def login_and_get_session(self) -> tuple[list[SetCookieParam], str] | None:
        """Login to KDP careers site and get session."""
        self.logger.info("Starting KDP login process (Navigating to site)")
        session_user_agent = self.get_user_agent()

        # This site appears to allow job searching without login
        # But we'll implement browser navigation to properly handle any session state/cookies

        browser = None
        context = None
        page: Page | None = None # Type hint

        with sync_playwright() as p:
            proxy_config = self.get_proxy_config()
            try:
                browser = p.chromium.launch(
                    proxy=proxy_config, headless=self.headless, timeout=self.browser_timeout_ms  # type: ignore[arg-type]
                )

                context = browser.new_context(
                    # proxy=proxy_config, # Setting at launch level
                    viewport={"width": 1920, "height": 1080},
                    user_agent=session_user_agent,
                    java_script_enabled=True,
                    accept_downloads=False,
                    ignore_https_errors=True,
                )

                context.set_default_navigation_timeout(self.browser_timeout_ms)
                page = context.new_page()

                # Navigate to careers page
                career_url = "https://careers.keurigdrpepper.com/en"
                self.logger.info(f"Navigating to careers page: {career_url}")
                page.goto(
                    career_url, wait_until="domcontentloaded", timeout=self.browser_timeout_ms
                )
                self.add_human_delay(2, 4)

                # Get cookies
                playwright_cookies: list[PlaywrightCookie] = context.cookies()

                # Convert cookies to SetCookieParam format
                cookies: list[SetCookieParam] = []
                for pc in playwright_cookies:
                    # Use .get() for potentially missing keys from PlaywrightCookie TypedDict
                    cookie: SetCookieParam = {
                        "name": pc.get("name", ""),
                        "value": pc.get("value", ""),
                        "domain": pc.get("domain", ""),
                        "path": pc.get("path", ""),
                    }
                    if not cookie["name"] or not cookie["value"]:
                        self.logger.warning(f"Skipping cookie with missing name/value: {pc}")
                        continue

                    # Optional fields
                    if pc.get("expires", -1) != -1:
                        expires = pc.get("expires")
                        if expires is not None:  # Explicit check for None before assignment
                            cookie["expires"] = expires
                    if "httpOnly" in pc:
                        cookie["httpOnly"] = pc["httpOnly"]
                    if "secure" in pc:
                        cookie["secure"] = pc["secure"]
                    if "sameSite" in pc and pc["sameSite"] in ["Lax", "None", "Strict"]:
                        cookie["sameSite"] = pc["sameSite"]
                    cookies.append(cookie)

                self.logger.info(f"Successfully got KDP session, {len(cookies)} cookies obtained.")
                return cookies, session_user_agent

            except PlaywrightTimeoutError as te:
                self.logger.error(f"Timeout during Playwright operation: {te}")
                if page:
                    page.screenshot(path=self.output_dir / "kdp_timeout_error.png")
                return None

            except PlaywrightError as pe:
                self.logger.error(f"Playwright error during KDP navigation: {pe}")
                return None

            except Exception as e:
                self.logger.error(f"Unexpected error during KDP navigation: {e}", exc_info=True)
                return None

            finally:
                if context:
                    context.close()
                if browser:
                    browser.close()

    def validate_session(self, cookies: Sequence[SetCookieParam], user_agent: str) -> bool:
        """Validate KDP session cookies by accessing search page."""
        # Session validation is less critical for KDP as authentication isn't strictly required
        # Check if we can access the search page
        headers = {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml",
        }

        # Convert SetCookieParam sequence to simple dict for requests
        cookie_dict = {cookie["name"]: cookie["value"] for cookie in cookies}

        try:
            response = requests.get(
                self.search_url, headers=headers, cookies=cookie_dict, timeout=self.request_timeout
            )

            # Check if response is successful and contains expected job search content
            if response.status_code == 200 and "Search Open Jobs" in response.text:
                self.logger.info("KDP session validation successful")
                return True
            else:
                self.logger.warning(
                    f"KDP session validation failed: Status code {response.status_code}"
                )
                return False

        except requests.exceptions.RequestException as e:
            self.logger.warning(f"KDP session validation failed due to network error: {e}")
            return False

    def load_existing_job_ids(self) -> set[str]:
        """Load existing job IDs from CSV file."""
        existing_jobs = set()

        if not self.csv_file_path.exists():
            self.logger.info(f"CSV file {self.csv_file_path} not found. Starting fresh.")
            return existing_jobs

        try:
            with open(self.csv_file_path, newline="", encoding="utf-8") as csvfile:
                # Check if file is empty
                first_line = csvfile.readline()
                if not first_line:
                    return existing_jobs

                csvfile.seek(0)  # Reset to beginning

                reader = csv.DictReader(csvfile)
                if not reader.fieldnames or "Job ID" not in reader.fieldnames:
                    self.logger.error(
                        f"CSV file {self.csv_file_path} is missing required header ('Job ID')."
                    )
                    return existing_jobs

                for row in reader:
                    if job_id := row.get("Job ID"):
                        existing_jobs.add(job_id)

            self.logger.info(
                f"Read {len(existing_jobs)} existing job IDs from {self.csv_file_path}"
            )

        except Exception as e:
            self.logger.error(f"Error reading existing job data from {self.csv_file_path}: {e}")

        return existing_jobs

    def save_jobs_to_csv(self, jobs: list[KeurigDrPepperJob]) -> None:
        """Save jobs to CSV file."""
        fieldnames = [
            "Job ID",
            "Job Title",
            "Date First Seen (UTC)",
            "Job Category",
            "Job Level",
            "Location",
            "Company",
            "Salary",
            "Position Type",
            "Job URL",
        ]

        new_jobs_count = 0
        is_new_file = not self.csv_file_path.exists() or self.csv_file_path.stat().st_size == 0

        try:
            with open(self.csv_file_path, mode="a", newline="", encoding="utf-8") as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

                if is_new_file:
                    writer.writeheader()
                    self.logger.info(f"Created new CSV file: {self.csv_file_path}")

                for job in jobs:
                    if job.job_id and job.job_id not in self.existing_job_ids:
                        writer.writerow(
                            {
                                "Job ID": job.job_id,
                                "Job Title": job.title,
                                "Date First Seen (UTC)": datetime.now(UTC)
                                .isoformat(timespec="seconds")
                                .replace("+00:00", "Z"),
                                "Job Category": job.job_category,
                                "Job Level": job.job_level,
                                "Location": job.location,
                                "Company": job.company,
                                "Salary": job.salary,
                                "Position Type": job.position_type,
                                "Job URL": job.url,
                            }
                        )

                        new_jobs_count += 1
                        self.existing_job_ids.add(job.job_id)

            if new_jobs_count > 0:
                self.logger.info(f"Added {new_jobs_count} new job entries to {self.csv_file_path}")

        except Exception as e:
            self.logger.error(f"Error writing to CSV file {self.csv_file_path}: {e}")

    def _extract_job_id_from_url(self, url: str) -> str:
        """Extract job ID from KDP job URL."""
        # Example URL: /en/job/essex-junction/fork-lift-operator-flex/42849/78642121456
        # ID is the last segment
        if not url:
            return ""

        segments = url.strip("/").split("/")
        if segments and segments[-1].isdigit():
            return segments[-1]
        return ""

    def scrape_jobs(self, analyze_all: bool = False) -> Sequence[KeurigDrPepperJob]: # Use Sequence
        """Scrape jobs from Keurig Dr Pepper careers site."""
        self.logger.info("--- Starting Keurig Dr Pepper Job Scraper ---")

        # Get session
        session_info = self.get_or_refresh_session()
        if not session_info:
            raise RuntimeError("Failed to establish a valid session.")

        cookies, user_agent = session_info
        self.existing_job_ids = self.load_existing_job_ids()

        # We'll use Playwright for more reliable job scraping
        browser = None
        context = None
        page: Page | None = None
        kdp_jobs: list[KeurigDrPepperJob] = [] # Ensure type

        with sync_playwright() as p:
            proxy_config = self.get_proxy_config()
            try:
                browser = p.chromium.launch(
                    proxy=proxy_config, headless=self.headless, timeout=self.browser_timeout_ms  # type: ignore[arg-type]
                )

                context = browser.new_context(
                    # proxy=proxy_config, # At launch level
                    viewport={"width": 1920, "height": 1080},
                    user_agent=user_agent,
                    java_script_enabled=True,
                    accept_downloads=False,
                    ignore_https_errors=True,
                )

                # Add cookies to context if any
                if cookies:
                    # Playwright context.add_cookies expects specific keys
                    # Convert our SetCookieParam list if needed, or ensure it's compatible
                    # The format derived earlier should be compatible
                    context.add_cookies(cookies)  # type: ignore[arg-type] # Assuming compatibility

                context.set_default_navigation_timeout(self.browser_timeout_ms)
                page = context.new_page()

                # Navigate to search page
                self.logger.info(f"Navigating to search page: {self.search_url}")
                page.goto(
                    self.search_url, wait_until="networkidle", timeout=self.browser_timeout_ms
                )
                self.add_human_delay(2, 3)

                # Apply filters if specified
                if self.location_filter:
                    self.logger.info(f"Applying location filter: {self.location_filter}")
                    
                    # First handle any cookie consent dialog that might appear
                    try:
                        cookie_accept = page.locator('button:has-text("Accept")').first
                        if cookie_accept and cookie_accept.is_visible(timeout=2000):
                            cookie_accept.click()
                            self.add_human_delay(1, 2)
                    except Exception:
                        self.logger.debug("No cookie dialog found or error handling it")
                    
                    # Wait for page to be fully loaded
                    page.wait_for_load_state("networkidle", timeout=self.browser_timeout_ms)
                    self.add_human_delay(2, 3)
                    
                    # Using simpler selectors based on visible elements from snapshot
                    try:
                        # Find the location input by looking for the paragraph containing "Location" label
                        # Then get the combobox within that paragraph
                        location_paragraph = page.locator('p:has-text("Location")').first
                        location_input = location_paragraph.locator('[role="combobox"]').first
                        
                        self.logger.info("Clicking location input field to focus")
                        location_input.click(timeout=5000) # Ensure focus
                        self.add_human_delay(0.5, 1.0)
                        
                        # Clear any existing text
                        self.logger.info("Clearing location input field")
                        location_input.fill("")
                        self.add_human_delay(0.5, 1.0)
                        
                        # Type slowly to trigger the dropdown
                        city_part = self.location_filter.split(',')[0].strip() if ',' in self.location_filter else self.location_filter
                        self.logger.info(f"Typing location slowly: {city_part}")
                        location_input.press_sequentially(city_part, delay=120) # Slightly increased delay between key presses
                        self.add_human_delay(1.0, 1.5) # Wait a bit after typing finishes
                        
                        # Wait for and select from dropdown
                        dropdown_interaction_successful = False
                        try:
                            self.logger.info("Explicitly waiting for location dropdown to be visible")
                            dropdown_locator = page.locator('div[role="listbox"]')
                            dropdown_locator.wait_for(state="visible", timeout=5000) # Explicit wait
                            
                            self.logger.info("Location dropdown is visible")
                            options = dropdown_locator.locator('a').all()
                            
                            if options:
                                exact_match_option = None
                                desired_location_text = self.location_filter.lower()
                                
                                for option_element in options:
                                    text_content = option_element.text_content()
                                    if text_content and desired_location_text in text_content.lower():
                                        exact_match_option = option_element
                                        break 
                                
                                if exact_match_option:
                                    self.logger.info(f"Found matching location in dropdown: {exact_match_option.text_content()}")
                                    exact_match_option.click()
                                    self.logger.info("Clicked matching location from dropdown.")
                                    dropdown_interaction_successful = True
                                else:
                                    self.logger.warning(f"No exact match for '{self.location_filter}' found in dropdown. Attempting to use first option.")
                                    options[0].click() # Click first if no exact match
                                    self.logger.info(f"Clicked first available option: {options[0].text_content()}")
                                    dropdown_interaction_successful = True
                            else:
                                self.logger.warning("No options found in dropdown even after waiting.")
                        except Exception as e:
                            self.logger.warning(f"Location dropdown did not appear or error selecting: {e}. Proceeding with typed text.")
                        
                        self.add_human_delay(1.5, 2.5)

                        # Ensure the radius is enabled and set, especially if dropdown interaction happened
                        if dropdown_interaction_successful: # Or always try if appropriate
                            try:
                                radius_select = page.locator('select[aria-label="Radius"]')
                                if radius_select.is_enabled(timeout=2000): # Check if enabled before selecting
                                    radius_select.select_option("50")
                                    self.logger.info("Set radius to 50 miles")
                                else:
                                    self.logger.debug("Radius select not enabled or not found shortly after location selection.")
                            except Exception as e:
                                self.logger.debug(f"Could not set radius: {e}")
                        else:
                            self.logger.info("Skipping radius setting as dropdown interaction was not confirmed successful.")
                            
                    except Exception as e: # Outer exception for the whole location block
                        self.logger.error(f"A critical error occurred while interacting with the location filter: {e}")
                        raise RuntimeError(f"Cannot interact with location filter: {e}")
                        
                    self.add_human_delay(1, 2) # Original delay after the entire location block

                # Click search button to apply location filter first
                search_button = page.locator('button:has-text("Search Jobs")').first
                self.logger.info("Clicking search button to apply initial filters")
                search_button.click()
                page.wait_for_load_state("networkidle", timeout=self.browser_timeout_ms)
                self.add_human_delay(2, 3)
                
                # Now apply Category filter if specified
                if self.category_filter:
                    raw_category_filter = self.category_filter.strip('"')
                    self.logger.info(f"Applying category filter: {raw_category_filter}")
                    
                    # Click on Category filter button to expand options
                    try:
                        category_button = page.locator('button:has-text("Category ")').first
                        category_button.click(timeout=5000)
                        self.add_human_delay(1, 2)
                        
                        # Find the label/text element, then its parent li, then the checkbox
                        try:
                            # Find the text element (label or span) that exactly matches the filter string
                            # This assumes the text is directly visible and unique enough.
                            text_element = page.get_by_text(raw_category_filter, exact=True).first
                            text_element.wait_for(state="visible", timeout=3000) # Ensure text element is visible

                            # Get the parent list item (li)
                            # XPath to find the closest ancestor li element
                            category_item_li = text_element.locator("xpath=ancestor::li[1]").first 

                            # Find the checkbox within this list item
                            category_checkbox = category_item_li.locator('input[type="checkbox"]').first
                            
                            if category_checkbox.is_visible(timeout=1000):
                                category_checkbox.click()
                                self.logger.info(f"Selected category: {raw_category_filter}")
                                self.add_human_delay(1, 2)
                            else:
                                self.logger.warning(f"Category checkbox for '{raw_category_filter}' found but not visible. Attempting to click text element as fallback.")
                                text_element.click() # Fallback: click the text element itself
                                self.logger.info(f"Clicked text element for category: {raw_category_filter}")
                                
                        except PlaywrightTimeoutError:
                            self.logger.error(f"Timeout finding or interacting with category filter elements for: {raw_category_filter}")
                        except Exception as e:
                            self.logger.error(f"Error selecting category '{raw_category_filter}': {e}")

                    except Exception as e:
                        self.logger.warning(f"Failed to expand or apply category filter: {e}")

                # Apply Job Level filter if specified
                if self.job_level_filter:
                    raw_job_level_filter = self.job_level_filter.strip('"')
                    self.logger.info(f"Applying job level filter: {raw_job_level_filter}")
                    
                    try:
                        job_level_button = page.locator('button:has-text("Job Level ")').first
                        job_level_button.click(timeout=5000)
                        self.add_human_delay(1, 2)
                        
                        # Find the label/text element, then its parent li, then the checkbox
                        try:
                            text_element = page.get_by_text(raw_job_level_filter, exact=True).first
                            text_element.wait_for(state="visible", timeout=3000)

                            job_level_item_li = text_element.locator("xpath=ancestor::li[1]").first
                            job_level_checkbox = job_level_item_li.locator('input[type="checkbox"]').first
                            
                            if job_level_checkbox.is_visible(timeout=1000):
                                job_level_checkbox.click()
                                self.logger.info(f"Selected job level: {raw_job_level_filter}")
                                self.add_human_delay(1, 2)
                            else:
                                self.logger.warning(f"Job level checkbox for '{raw_job_level_filter}' found but not visible. Attempting to click text element.")
                                text_element.click()
                                self.logger.info(f"Clicked text element for job level: {raw_job_level_filter}")
                                
                        except PlaywrightTimeoutError:
                            self.logger.error(f"Timeout finding or interacting with job level filter elements for: {raw_job_level_filter}")
                        except Exception as e:
                            self.logger.error(f"Error selecting job level '{raw_job_level_filter}': {e}")
                            
                    except Exception as e:
                        self.logger.warning(f"Failed to expand or apply job level filter: {e}")

                # If any category or job level filter was applied, click search again
                if self.category_filter or self.job_level_filter:
                    self.logger.info("Clicking search button to apply additional filters")
                    try:
                        search_button = page.locator('button:has-text("Search Jobs")').first
                        search_button.click()
                        self.logger.info("Search with filters submitted, waiting for results...")
                        page.wait_for_load_state("networkidle", timeout=self.browser_timeout_ms)
                        self.add_human_delay(3, 5)
                    except Exception as e:
                        self.logger.error(f"Failed to click search button for filters: {e}")

                # Get total job count
                total_jobs_text = page.locator('h1:has-text("Jobs")').text_content() or "0 Jobs"
                total_jobs_count = (
                    int(total_jobs_text.split()[0]) if total_jobs_text.split()[0].isdigit() else 0
                )
                self.logger.info(f"Found {total_jobs_count} jobs in search results")

                # Process all pages
                current_page = 1
                total_pages = (total_jobs_count + 14) // 15  # Assuming 15 jobs per page

                while current_page <= total_pages:
                    self.logger.info(f"Processing page {current_page} of {total_pages}")

                    # Get all job listings on current page
                    job_links = page.locator('a[href^="/en/job/"]').all()
                    self.logger.info(f"Found {len(job_links)} job links on page {current_page}")

                    for i, job_link in enumerate(job_links):
                        job_title, job_location, job_category = "Unknown Title", "Unknown Location", "Unknown Category"
                        try:
                            # Extract job title
                            job_title_el = job_link.locator("> h2").first
                            if job_title_el.is_visible(timeout=1500):
                                job_title = job_title_el.text_content() or "Unknown Title (empty text)"
                            else:
                                self.logger.debug(f"Job link {i}: Title H2 not visible.")
                                # Try to get title from the link's aria-label or text if H2 fails
                                link_text = job_link.text_content()
                                if link_text and len(link_text.split(' ')) > 2: # Heuristic for a title-like string
                                    job_title = link_text.split('\n')[0].strip() # Often titles are first line of link text
                                    self.logger.debug(f"Job link {i}: Using link text as fallback title: {job_title}")
                                else:    
                                    self.logger.warning(f"Job link {i}: Title H2 not found/visible, and link text not suitable. Skipping.")
                                    continue

                            # Extract job location
                            job_location_el = job_link.locator("> generic").nth(0)
                            if job_location_el.is_visible(timeout=1000):
                                job_location = job_location_el.text_content() or "Unknown Location (empty text)"
                            else:
                                self.logger.debug(f"Job link {i} (Title: {job_title}): Location element not visible.")

                            # Extract category
                            job_category_el = job_link.locator("> generic").nth(1)
                            if job_category_el.is_visible(timeout=1000):
                                job_category = job_category_el.text_content() or "Unknown Category (empty text)"
                            else:
                                self.logger.debug(f"Job link {i} (Title: {job_title}): Category element not visible.")
                            
                            # Extract job ID from the URL
                            href = job_link.get_attribute("href")
                            job_id = href.split("/")[-1] if href and "/" in href else "unknown"
                            
                            self.logger.info(f"Getting details for job: {job_title} ({job_id})")
                            
                            # Open job detail in a new page
                            with page.context.new_page() as job_page:
                                job_url = f"{self.base_url}{href}"
                                job_page.goto(job_url, wait_until="domcontentloaded", timeout=self.browser_timeout_ms)
                                self.add_human_delay(2, 3)
                                
                                # Extract job details
                                try:
                                    # Get job description
                                    description_el = job_page.locator('.job-description').first
                                    description = description_el.inner_html(timeout=10000) if description_el else ""
                                    
                                    # Get job level
                                    job_level = ""
                                    try:
                                        level_el = job_page.locator('div:has-text("Job Level:")').first
                                        if level_el:
                                            level_text = level_el.text_content(timeout=5000)
                                            if level_text and ":" in level_text:
                                                job_level = level_text.split(":", 1)[1].strip()
                                    except Exception as level_err:
                                        self.logger.debug(f"Could not extract job level: {level_err}")
                                    
                                    # Create job object
                                    job = KeurigDrPepperJob(
                                        job_id=job_id,
                                        title=job_title or "Unknown Title",
                                        company="Keurig Dr Pepper",
                                        location=job_location or "Unknown Location",
                                        date_posted=datetime.now().strftime("%Y-%m-%d"),
                                        url=job_url or "",
                                        description=description or "",
                                        job_category=job_category or "Uncategorized",
                                        job_level=job_level or "",
                                        position_type="",
                                        requirements=[],
                                        is_new=True,
                                        status=JobStatus.NEW
                                    )
                                    
                                    # Add job to results
                                    kdp_jobs.append(job)
                                    self.logger.debug(f"Added job: {job_title} - {job_location}")
                                    
                                except Exception as detail_err:
                                    self.logger.warning(f"Error extracting job details: {detail_err}")
                        
                        except PlaywrightTimeoutError as te:
                            # This specific timeout exception might not be hit as often due to is_visible checks
                            self.logger.warning(f"General PlaywrightTimeoutError for job link {i} (Title: {job_title}): {te.message.splitlines()[0]}")
                            continue # Skip to next job link
                        except Exception as e:
                            self.logger.error(f"Unexpected error processing job link {i} (Title: {job_title}): {e}")
                            continue # Skip to next job link

                    # Check if there are more pages
                    if current_page < total_pages:
                        # Click next page button
                        next_button = page.locator('a:has-text("Next")').first
                        if next_button and next_button.is_visible():
                            next_button.click()
                            self.logger.info("Navigating to next page...")
                            page.wait_for_load_state("networkidle", timeout=self.browser_timeout_ms)
                            self.add_human_delay(3, 5)
                            current_page += 1
                        else:
                            self.logger.warning("Next page button not found or not visible.")
                            break
                    else:
                        break

                # Save to CSV
                self.logger.info(
                    f"Found {len(kdp_jobs)} jobs, {sum(1 for job in kdp_jobs if job.is_new)} are new."
                )
                self.save_jobs_to_csv(kdp_jobs)

                # Update new job IDs
                self.new_job_ids = {job.job_id for job in kdp_jobs if job.is_new}

                # Sort by new > date posted > title
                kdp_jobs.sort(
                    key=lambda x: (x.is_new, x.date_posted, x.title),
                    reverse=True,
                )
                self.jobs_found = kdp_jobs # Store final list (assignment is covariant)
                self.logger.info(f"--- Keurig Dr Pepper Scraper finished. Found {len(self.jobs_found)} jobs. ---")
                return kdp_jobs # Return the specific list

            except PlaywrightTimeoutError as te:
                self.logger.error(f"Timeout during KDP job scraping: {te}")
                if page:
                    page.screenshot(path=self.output_dir / "kdp_scraping_timeout_error.png")
                raise RuntimeError(f"Timeout during KDP job scraping: {te}") from te

            except PlaywrightError as pe:
                self.logger.error(f"Playwright error during KDP job scraping: {pe}")
                raise RuntimeError(f"Playwright error during KDP job scraping: {pe}") from pe

            except Exception as e:
                self.logger.error(f"Unexpected error during KDP job scraping: {e}", exc_info=True)
                raise RuntimeError(f"Unexpected error during KDP job scraping: {e}") from e

            finally:
                # Close all resources
                if context:
                    context.close()
                if browser:
                    browser.close()

    def generate_report(self, jobs: Sequence[Job], report_file: Path) -> None:
        """Generate an HTML report from KDP job data."""
        # Type check that all jobs are KeurigDrPepperJob instances
        kdp_jobs = [job for job in jobs if isinstance(job, KeurigDrPepperJob)]
        if len(kdp_jobs) != len(jobs):
            self.logger.warning(f"Expected all jobs to be KeurigDrPepperJob instances, but found {len(jobs) - len(kdp_jobs)} other types")

        timestamp_dt = datetime.now(UTC)
        # timestamp_str = timestamp_dt.strftime("%Y%m%d_%H%M%S") # F841: Unused
        # iso_timestamp_str = timestamp_dt.isoformat(timespec="seconds").replace("+00:00", "Z") # F841: Unused

        # Convert UTC timestamp to local time (America/Chicago)
        cst = pytz.timezone("America/Chicago")
        dt_cst = timestamp_dt.astimezone(cst)
        formatted_timestamp = dt_cst.strftime("%Y-%m-%d %H:%M:%S %Z")

        # Count jobs by type
        category_counts = {}
        for job in kdp_jobs:
            category = job.job_category.strip() if job.job_category else "Other"
            category_counts[category] = category_counts.get(category, 0) + 1

        # HTML generation
        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Keurig Dr Pepper Job Report - {formatted_timestamp}</title>
    <style>
        /* CSS styles */
        body {{ font-family: sans-serif; margin: 20px; }}
        h1, h2 {{ color: #333; }}
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
        .salary, .location {{ white-space: nowrap; }}

        /* Styling for the expandable content */
        .job-row {{
            cursor: pointer;
        }}
        .job-row:hover {{
            background-color: #f0f8ff;
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
            background-color: #f2f2f2;
            font-weight: bold;
            font-size: 14px;
        }}

        /* Styling for new job highlighting */
        .new-job .title-cell {{
            background-color: #f0fff0;
        }}
        .new-tag {{
            display: inline-block;
            background-color: #28a745;
            color: white;
            padding: 2px 6px;
            font-size: 0.75em;
            font-weight: bold;
            border-radius: 4px;
            margin-right: 5px;
            vertical-align: middle;
        }}

        /* Category summary */
        .category-summary {{
            margin: 20px 0;
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
        }}
        .category-card {{
            background-color: #f8f9fa;
            border: 1px solid #ddd;
            border-radius: 4px;
            padding: 10px;
            min-width: 180px;
        }}
        .category-name {{
            font-weight: bold;
            margin-bottom: 5px;
        }}
        .category-count {{
            color: #28a745;
            font-weight: bold;
        }}
    </style>
</head>
<body>
    <h1>Keurig Dr Pepper Job Report</h1>
    <p>Generated: {formatted_timestamp}</p>
    <p>Filters: Location = "{self.location_filter or 'Any'}", Keywords = "{self.category_filter or 'Any'}", Job Level = "{self.job_level_filter or 'Any'}"</p>
    <p>Found {len(kdp_jobs)} jobs, {sum(1 for job in kdp_jobs if job.is_new)} are new.</p>

    <h2>Jobs by Category</h2>
    <div class="category-summary">
"""

        # Add category summary cards
        for category, count in sorted(category_counts.items()):
            html_content += f"""
        <div class="category-card">
            <div class="category-name">{category}</div>
            <div class="category-count">{count} jobs</div>
        </div>"""

        # Add job table
        html_content += """
    </div>

    <h2>All Jobs</h2>
    <table id="jobTable">
        <thead>
            <tr>
                <th>Title</th>
                <th>Category</th>
                <th>Location</th>
                <th>Job Level</th>
                <th>Position Type</th>
            </tr>
        </thead>
        <tbody>
"""

        # Add job rows
        for idx, job in enumerate(kdp_jobs, 1):
            new_indicator_html = '<span class="new-tag">NEW</span> ' if job.is_new else ""
            row_class = "job-row new-job" if job.is_new else "job-row"

            html_content += f"""
            <tr class="{row_class}" data-job-id="{idx}">
                <td class="title-cell"><span class="expander">+</span> {new_indicator_html}<a href="{job.url}" target="_blank">{job.title}</a></td>
                <td>{job.job_category}</td>
                <td class="location">{job.location}</td>
                <td>{job.job_level}</td>
                <td>{job.position_type}</td>
            </tr>
            <tr class="description-row" id="job-{idx}" style="display:none;">
                <td colspan="5" class="description-container">
                    <div class="job-description">
"""

            # Add salary if specified
            if job.salary and job.salary != "Not specified":
                html_content += f"<p><strong>Salary:</strong> {job.salary}</p>"

            # Add requirements if available
            if job.requirements:
                html_content += "<p><strong>Requirements:</strong></p><ul>"
                for req in job.requirements:
                    html_content += f"<li>{req}</li>"
                html_content += "</ul>"

            # Add full description
            html_content += f"""
                        <div>{job.description}</div>
                    </div>
                </td>
            </tr>
"""

        # Add footer and JavaScript
        html_content += """
        </tbody>
    </table>
    <script>
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

        # Write the HTML report
        try:
            with open(report_file, "w", encoding="utf-8") as f:
                f.write(html_content)

            self.logger.info(f"Generated HTML report at: {report_file.resolve()}")

        except Exception as e:
            self.logger.error(f"Failed to generate/save HTML report: {e}")


# Multi-site job scraper manager
class JobScraperManager:
    """Manager for coordinating multiple job scrapers."""

    def __init__(self, config: Config, llm_debug: bool = False):
        """Initialize the job scraper manager.

        Args:
            config: Configuration manager
            llm_debug: Enable debug logging for LLM operations
        """
        self.config = config
        self.llm_debug = llm_debug
        self.logger = logging.getLogger(__name__)

        # Path settings
        self.output_dir = Path(config.get("OUTPUT_DIR", "output"))
        self.docs_dir = Path(config.get("DOCS_DIR", "docs"))

        # Create directories
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.docs_dir.mkdir(parents=True, exist_ok=True)

        # Initialize scrapers
        self.scrapers: dict[str, JobScraper] = {}

        # Initialize scrapers based on config
        if config.get("ROBERTHALF_ENABLED", True): # Default RH to enabled if not specified
            self.scrapers["robert_half"] = RobertHalfScraper(config, llm_debug)
            self.logger.info("Robert Half scraper enabled.")
        else:
            self.logger.info("Robert Half scraper disabled by config.")

        if config.get("KDP_ENABLED", False): # Default KDP to disabled if not specified
            self.scrapers["kdp"] = KeurigDrPepperScraper(config, llm_debug)
            self.logger.info("Keurig Dr Pepper scraper enabled.")
        else:
             self.logger.info("Keurig Dr Pepper scraper disabled by config.")

        if not self.scrapers:
             self.logger.warning("No job scrapers are enabled in the configuration!")

    def run_scrapers(self, analyze_all: bool = False) -> dict[str, list[Job]]:
        """Run all enabled scrapers and return the job results.

        Args:
            analyze_all: Analyze all jobs, not just new ones

        Returns:
            Dictionary of scraper name to list of jobs found
        """
        results = {}

        for name, scraper in self.scrapers.items():
            self.logger.info(f"Running {name} scraper...")
            try:
                jobs = scraper.scrape_jobs(analyze_all=analyze_all)
                self.logger.info(f"{name} scraper found {len(jobs)} jobs.")

                # Generate report
                report_file = self.docs_dir / f"{name}_jobs.html"
                # Ensure jobs type matches generate_report signature
                scraper.generate_report(jobs, report_file)

                results[name] = jobs

            except Exception as e:
                self.logger.error(f"Error running {name} scraper: {e}", exc_info=True)
                results[name] = []

        return results

    # Add the run method here (moved from RobertHalfScraper)
    def run(self, analyze_all: bool = False) -> None:
        """Run all scrapers and generate reports.

        Args:
            analyze_all: Analyze all jobs, not just new ones
        """
        start_time = time.time()
        self.logger.info(f"Starting job scraper manager with {len(self.scrapers)} enabled scrapers")

        # Run all scrapers
        all_jobs = self.run_scrapers(analyze_all=analyze_all)

        # Generate combined report
        if all_jobs:
            # Only generate if there are actually jobs from any source
            combined_job_list = [job for jobs in all_jobs.values() for job in jobs]
            if combined_job_list:
                self.generate_combined_report(all_jobs)
            else:
                 self.logger.info("No jobs found by any scraper. Skipping combined report.")

        # Log completion
        end_time = time.time()
        total_jobs = sum(len(jobs) for jobs in all_jobs.values())
        total_new_jobs = sum(sum(1 for job in jobs if job.is_new) for jobs in all_jobs.values())

        self.logger.info(f"Job scraping completed in {end_time - start_time:.2f} seconds")
        self.logger.info(
            f"Found {total_jobs} jobs across {len(all_jobs)} sources, with {total_new_jobs} new jobs"
        )

    def generate_combined_report(self, all_jobs: dict[str, list[Job]]) -> None:
        """Generate a combined HTML report from all job sources.

        Args:
            all_jobs: Dictionary of scraper name to list of jobs found
        """
        timestamp_dt = datetime.now(UTC)
        # iso_timestamp_str = timestamp_dt.isoformat(timespec="seconds").replace("+00:00", "Z") # F841: Unused

        # Convert UTC timestamp to local time (America/Chicago)
        try:
            cst = pytz.timezone("America/Chicago")
            dt_cst = timestamp_dt.astimezone(cst)
            formatted_timestamp = dt_cst.strftime("%Y-%m-%d %H:%M:%S %Z")
        except pytz.UnknownTimeZoneError:
             self.logger.warning("Timezone 'America/Chicago' not found. Using UTC time.")
             formatted_timestamp = timestamp_dt.strftime("%Y-%m-%d %H:%M:%S UTC")
             dt_cst = timestamp_dt  # Fallback for filename

        # Count total jobs and new jobs
        total_jobs = sum(len(jobs) for jobs in all_jobs.values())
        total_new_jobs = sum(sum(1 for job in jobs if job.is_new) for jobs in all_jobs.values())

        # HTML generation
        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Combined Job Report - {formatted_timestamp}</title>
    <style>
        /* CSS styles */
        body {{ font-family: sans-serif; margin: 20px; }}
        h1, h2, h3 {{ color: #333; }}
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
        .job-source {{
            display: inline-block;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 0.8em;
            margin-right: 8px;
        }}
        .source-roberthalf {{ background-color: #e6f7ff; color: #0066cc; }}
        .source-kdp {{ background-color: #e6fff2; color: #00994d; }}

        /* Styling for the expandable content */
        .job-row {{
            cursor: pointer;
        }}
        .job-row:hover {{
            background-color: #f0f8ff;
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
            background-color: #f2f2f2;
            font-weight: bold;
            font-size: 14px;
        }}

        /* Styling for new job highlighting */
        .new-job .title-cell {{
            background-color: #f0fff0;
        }}
        .new-tag {{
            display: inline-block;
            background-color: #28a745;
            color: white;
            padding: 2px 6px;
            font-size: 0.75em;
            font-weight: bold;
            border-radius: 4px;
            margin-right: 5px;
            vertical-align: middle;
        }}

        /* Summary cards */
        .summary-cards {{
            display: flex;
            flex-wrap: wrap;
            gap: 20px;
            margin-bottom: 20px;
        }}
        .summary-card {{
            background-color: #f8f9fa;
            border: 1px solid #ddd;
            border-radius: 4px;
            padding: 15px;
            min-width: 200px;
            flex: 1;
        }}
        .card-title {{
            font-weight: bold;
            margin-bottom: 10px;
            font-size: 1.1em;
        }}
        .card-value {{
            font-size: 2em;
            font-weight: bold;
            color: #28a745;
        }}
        .card-subtitle {{
            color: #666;
            font-size: 0.9em;
            margin-top: 5px;
        }}

        /* Tabs for job sources */
        .tabs {{
            display: flex;
            margin: 20px 0;
            border-bottom: 1px solid #ddd;
        }}
        .tab {{
            padding: 10px 20px;
            cursor: pointer;
            border: 1px solid #ddd;
            border-bottom: none;
            margin-right: 5px;
            border-radius: 4px 4px 0 0;
            background-color: #f8f9fa;
        }}
        .tab.active {{
            background-color: white;
            border-bottom: 2px solid white;
            font-weight: bold;
            position: relative;
            top: 1px;
        }}
        .tab-content {{
            display: none;
        }}
        .tab-content.active {{
            display: block;
        }}
    </style>
</head>
<body>
    <h1>Combined Job Report</h1>
    <p>Generated: {formatted_timestamp}</p>
    <p>Found {total_jobs} jobs across {len(all_jobs)} sources, with {total_new_jobs} new jobs.</p>

    <div class="summary-cards">
        <div class="summary-card">
            <div class="card-title">Total Jobs</div>
            <div class="card-value">{total_jobs}</div>
            <div class="card-subtitle">Across all sources</div>
        </div>
        <div class="summary-card">
            <div class="card-title">New Jobs</div>
            <div class="card-value">{total_new_jobs}</div>
            <div class="card-subtitle">Since last check</div>
        </div>
        <div class="summary-card">
            <div class="card-title">Sources</div>
            <div class="card-value">{len(all_jobs)}</div>
            <div class="card-subtitle">Job platforms</div>
        </div>
    </div>

    <div class="tabs">
        <div class="tab active" data-tab="all">All Jobs</div>
"""

        # Add tabs for each source
        for _i, source in enumerate(all_jobs.keys(), 1): # B007: Use _i for unused variable
            source_name = source.replace("_", " ").title()
            source_job_count = len(all_jobs[source])
            html_content += f'        <div class="tab" data-tab="{source}">{source_name} ({source_job_count})</div>\n'

        # Start tab content - All Jobs
        html_content += """
    </div>

    <div class="tab-content active" id="all">
        <table class="job-table">
            <thead>
                <tr>
                    <th>Source / Title</th>
                    <th>Location</th>
                    <th>Category</th>
                    <th>Type</th>
                    <th>Salary</th>
                </tr>
            </thead>
            <tbody>
"""

        # Add all jobs to the combined table
        all_combined_jobs = []
        for source, jobs in all_jobs.items():
            for job in jobs:
                all_combined_jobs.append((source, job))

        # Sort all jobs by new > date posted > title
        all_combined_jobs.sort(
            key=lambda x: (x[1].is_new, x[1].date_posted, x[1].title),
            reverse=True,
        )

        # Add combined job rows
        for idx, (source, job) in enumerate(all_combined_jobs, 1):
            new_indicator_html = '<span class="new-tag">NEW</span> ' if job.is_new else ""
            row_class = "job-row new-job" if job.is_new else "job-row"
            source_class_name = source.replace("_", "") # e.g., roberthalf, kdp
            source_display_name = source.replace("_", " ").title()
            source_display = f'<span class="job-source source-{source_class_name}">{source_display_name}</span>'

            # Extract category - different for each source
            category = "N/A" # Default
            if isinstance(job, RobertHalfJob):
                category = "Technology" # Assuming RHT is tech, adjust if needed
            elif isinstance(job, KeurigDrPepperJob):
                category = job.job_category if job.job_category else "N/A"

            # Extract job type
            job_type = job.job_type if job.job_type != "Not specified" else "N/A"

            html_content += f"""
            <tr class="{row_class}" data-job-id="all-{idx}">
                <td class="title-cell"><span class="expander">+</span> {source_display} {new_indicator_html}<a href="{job.url}" target="_blank">{job.title}</a></td>
                <td>{job.location}</td>
                <td>{category}</td>
                <td>{job_type}</td>
                <td>{job.salary}</td>
            </tr>
            <tr class="description-row" id="desc-all-{idx}" style="display:none;">
                <td colspan="5" class="description-container">
                    <div class="job-description">
                        <p><strong>Company:</strong> {job.company}</p>
                        <p><strong>Date Posted:</strong> {job.date_posted}</p>
                        <div>{job.description}</div>
                    </div>
                </td>
            </tr>
"""

        # Close the all jobs tab
        html_content += """
            </tbody>
        </table>
    </div>
"""

        # Add tab content for each source
        for source, jobs in all_jobs.items():
            source_name = source.replace("_", " ").title()
            html_content += f"""
    <div class="tab-content" id="{source}">
        <h2>{source_name} Jobs</h2>
        <p>Found {len(jobs)} jobs, {sum(1 for job in jobs if job.is_new)} are new.</p>
        <table class="job-table">
            <thead>
                <tr>
                    <th>Title</th>
                    <th>Location</th>
"""

            # Customize columns based on source
            if source == "robert_half":
                html_content += """
                    <th>Pay Rate</th>
                    <th>Job ID</th>
                    <th>Posted Date</th>
                </tr>
            </thead>
            <tbody>
"""
            elif source == "kdp":
                html_content += """
                    <th>Category</th>
                    <th>Job Level</th>
                    <th>Position Type</th>
                </tr>
            </thead>
            <tbody>
"""
            else: # Fallback for future sources
                html_content += """
                    <th>Job Type</th>
                    <th>Salary</th>
                    <th>Date Posted</th>
                </tr>
            </thead>
            <tbody>
"""

            # Add job rows for this source
            if not jobs:
                colspan = 5 # Default, adjust based on headers
                if source == "robert_half" or source == "kdp": colspan = 5
                html_content += f'<tr><td colspan="{colspan}">No jobs found for {source_name}.</td></tr>'

            for idx, job in enumerate(jobs, 1):
                new_indicator_html = '<span class="new-tag">NEW</span> ' if job.is_new else ""
                row_class = "job-row new-job" if job.is_new else "job-row"
                job_specific_id = f"{source}-{idx}" # Unique ID for description row

                if isinstance(job, RobertHalfJob):
                    html_content += f"""
            <tr class="{row_class}" data-job-id="{job_specific_id}">
                <td class="title-cell"><span class="expander">+</span> {new_indicator_html}<a href="{job.url}" target="_blank">{job.title}</a></td>
                <td>{job.location}</td>
                <td>{job.salary}</td>
                <td>{job.job_id}</td>
                <td>{job.date_posted}</td>
            </tr>
            <tr class="description-row" id="desc-{job_specific_id}" style="display:none;">
                <td colspan="5" class="description-container">
                    <div class="job-description">
                        {job.description if job.description else "<p>No description provided.</p>"}
                    </div>
                </td>
            </tr>
"""
                elif isinstance(job, KeurigDrPepperJob):
                    html_content += f"""
            <tr class="{row_class}" data-job-id="{job_specific_id}">
                <td class="title-cell"><span class="expander">+</span> {new_indicator_html}<a href="{job.url}" target="_blank">{job.title}</a></td>
                <td>{job.location}</td>
                <td>{job.job_category}</td>
                <td>{job.job_level}</td>
                <td>{job.position_type}</td>
            </tr>
            <tr class="description-row" id="desc-{job_specific_id}" style="display:none;">
                <td colspan="5" class="description-container">
                    <div class="job-description">
"""

                    # Add salary if specified
                    if job.salary and job.salary != "Not specified":
                        html_content += f"<p><strong>Salary:</strong> {job.salary}</p>"

                    # Add requirements if available
                    if job.requirements:
                        html_content += "<p><strong>Requirements:</strong></p><ul>"
                        for req in job.requirements:
                            html_content += f"<li>{req}</li>"
                        html_content += "</ul>"
                    elif job.description:
                         # Try to show description if no specific requirements section found
                         html_content += f"""
                         <p><strong>Description:</strong></p>
                         <div>{job.description}</div>"""
                    else:
                        html_content += "<p>No description or requirements provided.</p>"

                    # Closing div and td for description row
                    html_content += """
                    </div>
                </td>
            </tr>
"""
                else:
                    # Generic job type fallback
                    html_content += f"""
            <tr class="{row_class}" data-job-id="{job_specific_id}">
                <td class="title-cell"><span class="expander">+</span> {new_indicator_html}<a href="{job.url}" target="_blank">{job.title}</a></td>
                <td>{job.location}</td>
                <td>{job.job_type}</td>
                <td>{job.salary}</td>
                <td>{job.date_posted}</td>
            </tr>
            <tr class="description-row" id="desc-{job_specific_id}" style="display:none;">
                <td colspan="5" class="description-container">
                    <div class="job-description">
                        {job.description if job.description else "<p>No description provided.</p>"}
                    </div>
                </td>
            </tr>
"""

            # Close this source tab's table body and table
            html_content += """
            </tbody>
        </table>
    </div>
"""

        # Add JavaScript for tabs and expandable rows
        html_content += """
    <script>
        // Tab functionality
        document.addEventListener('DOMContentLoaded', function() {
            const tabs = document.querySelectorAll('.tab');

            tabs.forEach(tab => {
                tab.addEventListener('click', function() {
                    // Remove active class from all tabs
                    tabs.forEach(t => t.classList.remove('active'));

                    // Add active class to clicked tab
                    this.classList.add('active');

                    // Hide all tab content
                    document.querySelectorAll('.tab-content').forEach(content => {
                        content.classList.remove('active');
                    });

                    // Show the corresponding tab content
                    const tabId = this.getAttribute('data-tab');
                    document.getElementById(tabId).classList.add('active');
                });
            });

            // Expandable rows
            const jobRows = document.querySelectorAll('.job-row');
            jobRows.forEach(row => {
                row.addEventListener('click', function(event) {
                    // Prevent toggling if clicking on the link itself
                    if (event.target.tagName === 'A') {
                        return;
                    }

                    const jobId = this.getAttribute('data-job-id');
                    const descriptionRow = document.getElementById('desc-' + jobId); // Match ID prefix 'desc-'
                    const expander = this.querySelector('.expander');

                    if (descriptionRow && expander) {
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

        # Write the HTML report
        # Use a filename reflecting the combined nature and timestamp
        cst_filename_part = dt_cst.strftime("%Y%m%d_%H%M%S")
        report_file = self.docs_dir / f"combined_jobs_{cst_filename_part}.html"

        try:
            with open(report_file, "w", encoding="utf-8") as f:
                f.write(html_content)

            self.logger.info(f"Generated combined HTML report at: {report_file.resolve()}")

        except Exception as e:
            self.logger.error(f"Failed to generate/save combined HTML report: {e}")
