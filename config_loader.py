"""
Configuration loader utility for different environments.
"""
import logging
import os
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv

# Get a logger for this module
logger = logging.getLogger(__name__)

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
    if cleaned_value is None and default is not None:
        cleaned_value = default
        logger.debug(f"Using default value for {name}: {default}")
    return cleaned_value

def load_test_config() -> Dict[str, Any]:
    """Load test environment configuration from .env.test"""
    env_path = Path('.') / '.env.test'
    success = load_dotenv(dotenv_path=env_path, override=True)
    if not success:
        logger.warning(f"Could not find {env_path}. Using default environment variables.")
    else:
        logger.info(f"Loaded configuration from {env_path}")
    return load_config_values()

def load_prod_config() -> Dict[str, Any]:
    """Load production environment configuration from .env"""
    env_path = Path('.') / '.env'
    success = load_dotenv(dotenv_path=env_path, override=False)  # Don't override existing env vars
    if not success:
        logger.warning(f"Could not find {env_path}. Using default environment variables.")
    else:
        logger.info(f"Loaded configuration from {env_path}")
    return load_config_values()

def load_config_values() -> Dict[str, Any]:
    """Load and validate all configuration values."""
    config = {}
    
    # Session Management
    config['SAVE_SESSION'] = get_env_value('SAVE_SESSION', 'true').lower() == 'true'
    config['SESSION_FILE'] = get_env_value('SESSION_FILE', 'session_data.json')
    session_max_age = get_env_value('SESSION_MAX_AGE_HOURS', '12')
    try:
        config['SESSION_MAX_AGE_HOURS'] = int(session_max_age)
    except ValueError:
        logger.error(f"Invalid SESSION_MAX_AGE_HOURS value: {session_max_age}. Using default: 12")
        config['SESSION_MAX_AGE_HOURS'] = 12

    # Request Configuration
    request_delay = get_env_value('REQUEST_DELAY_SECONDS', '2')
    try:
        config['REQUEST_DELAY_SECONDS'] = float(request_delay)
    except ValueError:
        logger.error(f"Invalid REQUEST_DELAY_SECONDS value: {request_delay}. Using default: 2")
        config['REQUEST_DELAY_SECONDS'] = 2.0

    # Add other configuration values as needed...
    
    return config
