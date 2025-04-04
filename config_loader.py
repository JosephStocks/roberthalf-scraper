"""
Configuration loader utility for different environments.
"""
from dotenv import load_dotenv
from pathlib import Path
import os

def load_test_config():
    """Load test environment configuration from .env.test"""
    env_path = Path('.') / '.env.test'
    success = load_dotenv(dotenv_path=env_path)
    if not success:
        print(f"WARNING: Could not find {env_path}. Using default environment variables.")
    else:
        print(f"Loaded configuration from {env_path}")
    return success

def load_prod_config():
    """Load production environment configuration from .env"""
    env_path = Path('.') / '.env'
    success = load_dotenv(dotenv_path=env_path)
    if not success:
        print(f"WARNING: Could not find {env_path}. Using default environment variables.")
    else:
        print(f"Loaded configuration from {env_path}")
    return success
