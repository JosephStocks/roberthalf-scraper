import logging
import os

# Get a logger instance named after this module ("utils")
# It will inherit handlers/formatting from the root logger configured elsewhere
logger = logging.getLogger(__name__)

# Use the modern union type hint | None (equivalent to Optional)
def get_proxy_config() -> dict[str, str] | None:
    """
    Creates proxy configuration dictionary for Playwright/requests
    based on environment variables.

    Returns:
        A dictionary containing proxy settings ('server', 'username', 'password',
        optional 'bypass') if proxy is enabled and configured correctly,
        otherwise returns None.
    """
    use_proxy = os.getenv('USE_PROXY', 'false').lower() == 'true'
    if not use_proxy:
        logger.info("Proxy is disabled via USE_PROXY=false")
        return None

    proxy_server = os.getenv('PROXY_SERVER')
    proxy_auth = os.getenv('PROXY_AUTH')
    proxy_bypass = os.getenv('PROXY_BYPASS', None) # Default to None if not set

    if not proxy_server or not proxy_auth:
        logger.warning("Proxy configuration (PROXY_SERVER, PROXY_AUTH) is incomplete. Proxy disabled.")
        return None

    try:
        # Split only on the first colon to handle passwords potentially containing colons
        username, password = proxy_auth.split(':', 1)

        # Ensure scheme is present for Playwright, default to http
        server_url = proxy_server
        # Check if the server address already starts with a known scheme
        if not server_url.startswith(('http://', 'https://', 'socks://', 'socks5://', 'socks4://')):
             server_url = f"http://{proxy_server}"
             logger.debug(f"Prepending 'http://' to proxy server. Final server URL: {server_url}")

        # Use dict type hint here
        config: dict[str, str] = {
            "server": server_url,
            "username": username,
            "password": password,
        }

        # Only add bypass key if a value was provided in the environment
        if proxy_bypass:
            config["bypass"] = proxy_bypass

        logger.info(f"Proxy enabled: Server={config['server']}, User={config['username']}, Bypass={config.get('bypass', 'N/A')}")
        return config

    except ValueError:
        # Specific error if proxy_auth doesn't contain ':'
        logger.error("Error parsing PROXY_AUTH. Expected format 'username:password'. Proxy disabled.")
        return None
    except Exception as e:
        # Catch any other unexpected errors during config creation
        logger.error(f"Unexpected error creating proxy configuration: {e}. Proxy disabled.", exc_info=True) # Log traceback
        return None
