# filename: config_loader.py
"""
Configuration loader utility for different environments.
"""
import logging
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Get a logger for this module
# Initialize logger simply here; actual configuration happens in the main script
# This prevents circular dependencies if config is needed for logging setup.
logger = logging.getLogger(__name__)


# Helper function to load .env file
def _load_env(env_file: str, override: bool) -> bool:
    env_path = Path('.') / env_file
    success = load_dotenv(dotenv_path=env_path, override=override)
    if not success:
        logger.warning(f"Could not find {env_path}. Using default or system environment variables.")
    else:
        logger.info(f"Loaded configuration from {env_path}")
    return success

def validate_env_value(name: str, value: str | None) -> str | None:
    """Validate and clean environment variable value."""
    if value is None:
        return None

    # Strip any trailing comments and whitespace
    cleaned_value = value.split('#')[0].strip()
    return cleaned_value if cleaned_value else None

def get_env_value(name: str, default: str | None = None) -> str | None:
    """Get environment variable with validation."""
    value = os.getenv(name)
    cleaned_value = validate_env_value(name, value)
    if cleaned_value is None:
        if default is not None:
            # Don't log here, let the caller log with context
            # logger.debug(f"Using default value for {name}: {default}")
            return default
        else:
            return None # No value, no default
    return cleaned_value


def load_test_config() -> dict[str, Any]:
    """Load test environment configuration from .env.test"""
    _load_env('.env.test', override=True)
    return load_config_values()

def load_prod_config() -> dict[str, Any]:
    """Load production environment configuration from .env"""
    _load_env('.env', override=False) # Don't override existing env vars for prod
    return load_config_values()

def _get_typed_env_value(key: str, default: Any, value_type: type) -> Any:
    """Helper to get env value, convert type, log errors, and return default on failure."""
    str_value = get_env_value(key, str(default) if default is not None else None)
    if str_value is None:
        # Let the caller decide if warning is needed, this function just returns the default
        # logger.warning(f"Environment variable {key} not found. Using default: {default}")
        return default

    try:
        if value_type is bool:
            # Handle boolean conversion flexibly (e.g., 'true', '1', 'yes')
            return str_value.lower() in ('true', '1', 't', 'y', 'yes')
        elif value_type is Path:
             # Special handling for Path type if needed
             # For now, assume it's handled by the caller, but could be added here
             return Path(str_value) # Example Path conversion
        else:
            # Handle int, float, str
            return value_type(str_value)
    except (ValueError, TypeError) as e:
        logger.error(f"Invalid value for {key}: '{str_value}'. Expected type {value_type.__name__}. Error: {e}. Using default: {default}")
        return default


def load_config_values() -> dict[str, Any]:
    """Load, validate, and type-convert all configuration values from environment variables."""
    config = {}
    logger.info("Loading configuration values...")

    # === General ===
    config['TEST_MODE'] = _get_typed_env_value('TEST_MODE', False, bool)
    config['LOG_LEVEL'] = _get_typed_env_value('LOG_LEVEL', 'INFO', str).upper() # Ensure uppercase

    # === Session Management ===
    config['SAVE_SESSION'] = _get_typed_env_value('SAVE_SESSION', True, bool)
    config['SESSION_FILE'] = _get_typed_env_value('SESSION_FILE', 'session_data.json', str) # Keep as string, Path conversion later if needed
    config['SESSION_MAX_AGE_HOURS'] = _get_typed_env_value('SESSION_MAX_AGE_HOURS', 12, int)

    # === Scraping Parameters ===
    config['FILTER_STATE'] = _get_typed_env_value('FILTER_STATE', 'TX', str)
    config['JOB_POST_PERIOD'] = _get_typed_env_value('JOB_POST_PERIOD', 'PAST_24_HOURS', str)

    # === Browser / Playwright ===
    config['HEADLESS_BROWSER'] = _get_typed_env_value('HEADLESS_BROWSER', True, bool)
    config['ROTATE_USER_AGENT'] = _get_typed_env_value('ROTATE_USER_AGENT', False, bool)
    config['DEFAULT_USER_AGENT'] = _get_typed_env_value(
        'DEFAULT_USER_AGENT',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
        str
    )
    config['BROWSER_TIMEOUT_MS'] = _get_typed_env_value('BROWSER_TIMEOUT_MS', 60000, int)

    # === Delays & Timeouts ===
    config['REQUEST_DELAY_SECONDS'] = _get_typed_env_value('REQUEST_DELAY_SECONDS', 2.0, float)
    config['PAGE_DELAY_MIN'] = _get_typed_env_value('PAGE_DELAY_MIN', 5.0, float)
    config['PAGE_DELAY_MAX'] = _get_typed_env_value('PAGE_DELAY_MAX', 15.0, float)
    config['REQUEST_TIMEOUT_SECONDS'] = _get_typed_env_value('REQUEST_TIMEOUT_SECONDS', 30, int)

    # === Retries ===
    config['MAX_RETRIES'] = _get_typed_env_value('MAX_RETRIES', 3, int)

    # === Proxy Configuration ===
    config['USE_PROXY'] = _get_typed_env_value('USE_PROXY', False, bool)
    config['PROXY_SERVER'] = get_env_value('PROXY_SERVER')
    config['PROXY_AUTH'] = get_env_value('PROXY_AUTH')
    config['PROXY_BYPASS'] = get_env_value('PROXY_BYPASS')

    # === Pushover Notifications ===
    config['PUSHOVER_ENABLED'] = _get_typed_env_value('PUSHOVER_ENABLED', True, bool)
    config['PUSHOVER_TOKEN'] = get_env_value('PUSHOVER_TOKEN')
    config['PUSHOVER_USER_KEY_JOE'] = get_env_value('PUSHOVER_USER_KEY_JOE')
    config['PUSHOVER_USER_KEY_KATIE'] = get_env_value('PUSHOVER_USER_KEY_KATIE')

    # === RobertHalf Credentials ===
    config['ROBERTHALF_USERNAME'] = get_env_value('ROBERTHALF_USERNAME')
    config['ROBERTHALF_PASSWORD'] = get_env_value('ROBERTHALF_PASSWORD')

    # === Git & Report URL Configuration ===
    config['GITHUB_ACCESS_TOKEN'] = get_env_value('GITHUB_ACCESS_TOKEN')
    config['GITHUB_PAGES_URL'] = get_env_value('GITHUB_PAGES_URL')

    # === AI Job Matching Configuration ===
    config['MATCHING_ENABLED'] = _get_typed_env_value('MATCHING_ENABLED', False, bool)
    config['OPENAI_API_KEY'] = get_env_value('OPENAI_API_KEY') # Keep as string, or None
    config['CANDIDATE_PROFILE_PATH'] = _get_typed_env_value('CANDIDATE_PROFILE_PATH', 'candidate_profile.json', str)
    config['MATCHING_MODEL_TIER1'] = _get_typed_env_value('MATCHING_MODEL_TIER1', 'gpt-4o-mini', str)
    config['MATCHING_THRESHOLD_TIER1'] = _get_typed_env_value('MATCHING_THRESHOLD_TIER1', 60, int)
    config['MATCHING_MODEL_TIER2'] = _get_typed_env_value('MATCHING_MODEL_TIER2', 'gpt-4.1-mini', str)
    config['MATCHING_THRESHOLD_FINAL'] = _get_typed_env_value('MATCHING_THRESHOLD_FINAL', 75, int)

    # --- Validate Thresholds (0-100) ---
    for key in ['MATCHING_THRESHOLD_TIER1', 'MATCHING_THRESHOLD_FINAL']:
        threshold = config[key]
        if not (0 <= threshold <= 100):
            logger.warning(f"Configuration value for {key} ({threshold}) is outside the valid range [0, 100]. Clamping.")
            config[key] = max(0, min(100, threshold))

    # Log matching status
    if config['MATCHING_ENABLED']:
        logger.info(f"AI Job Matching Enabled: Tier1='{config['MATCHING_MODEL_TIER1']}' (Threshold {config['MATCHING_THRESHOLD_TIER1']}), Tier2='{config['MATCHING_MODEL_TIER2']}' (Final Threshold {config['MATCHING_THRESHOLD_FINAL']})")
        if not config['OPENAI_API_KEY']:
            logger.warning("MATCHING_ENABLED is true, but OPENAI_API_KEY is not set!")
    else:
        logger.info("AI Job Matching Disabled.")


    logger.info("Configuration loading complete.")
    # import json
    # logger.debug(f"Loaded configuration: {json.dumps(config, indent=2, default=str)}")

    return config


# --- Example Usage (optional, for testing the module) ---
# if __name__ == "__main__":
#     # Simple logging setup for testing this module directly
#     logging.basicConfig(level=logging.DEBUG, format='%(levelname)s [%(name)s] %(message)s')
#     print("--- Loading Prod Config ---")
#     prod_cfg = load_prod_config()
#     # print(json.dumps(prod_cfg, indent=2, default=str))
#
#     print("\n--- Loading Test Config ---")
#     test_cfg = load_test_config()
#     # print(json.dumps(test_cfg, indent=2, default=str))
#
#     # Example of accessing a value
#     print(f"\nTest Mode (Test): {test_cfg.get('TEST_MODE')}")
#     print(f"Session Max Age (Test): {test_cfg.get('SESSION_MAX_AGE_HOURS')}")
#     print(f"GitHub Pages URL (Test): {test_cfg.get('GITHUB_PAGES_URL')}") # Example check
