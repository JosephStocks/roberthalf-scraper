"""
Configuration loader utility for different environments.
"""
import logging # Import logging
from pathlib import Path

from dotenv import load_dotenv

# Get a logger for this module
logger = logging.getLogger(__name__)

def load_test_config():
    """Load test environment configuration from .env.test"""
    env_path = Path('.') / '.env.test'
    success = load_dotenv(dotenv_path=env_path)
    if not success:
        # Use logger instead of print
        logger.warning(f"Could not find {env_path}. Using default environment variables.")
    else:
        # Use logger instead of print
        logger.info(f"Loaded configuration from {env_path}")
    return success

def load_prod_config():
    """Load production environment configuration from .env"""
    env_path = Path('.') / '.env'
    success = load_dotenv(dotenv_path=env_path)
    if not success:
        # Use logger instead of print
        logger.warning(f"Could not find {env_path}. Using default environment variables.")
    else:
        # Use logger instead of print
        logger.info(f"Loaded configuration from {env_path}")
    return success
