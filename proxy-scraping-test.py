# --- proxy-scraping-test.py (Refactored) ---

import logging  # Use logging consistent with other modules
import os
import time

from playwright.sync_api import Error as PlaywrightError, sync_playwright

from config_loader import load_test_config
from utils import get_proxy_config  # Import the centralized function

# Basic logging setup for this script
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("proxy_test")

# Load test configuration variables into the environment
if not load_test_config():
    logger.warning("Could not load .env.test file. Relying on existing environment variables.")

def scrape_with_iproyal_proxy():
    logger.info("--- Starting Proxy Scraping Test ---")

    # --- Use the centralized proxy config function ---
    proxy_config = get_proxy_config()
    if not proxy_config:
        logger.error("Proxy is required for this test but is disabled or misconfigured in .env.test. Exiting.")
        return # Exit if no valid proxy config
    # --- End of proxy config block ---

    # Get optional configuration specific to this test
    try:
        max_requests_per_ip = int(os.getenv('MAX_REQUESTS_PER_IP', '5'))
        max_time_per_ip = int(os.getenv('MAX_TIME_PER_IP', '30'))  # seconds
        request_delay = float(os.getenv('REQUEST_DELAY', '2.0')) # Allow float for delay
        headless_mode = os.getenv('HEADLESS', 'false').lower() == 'true' # Optional headless for testing
        logger.info(f"Test Config: Max Req/IP={max_requests_per_ip}, Max Time/IP={max_time_per_ip}s, Delay={request_delay}s, Headless={headless_mode}")
    except ValueError as e:
        logger.error(f"Invalid non-integer value for test config (MAX_REQUESTS_PER_IP, MAX_TIME_PER_IP, REQUEST_DELAY): {e}")
        return

    with sync_playwright() as p:
        browser = None
        context = None
        try:
            # Launch browser with the obtained proxy_config
            logger.info(f"Launching browser with proxy: {proxy_config['server']} (Headless: {headless_mode})")
            browser = p.chromium.launch(
                proxy=proxy_config,
                headless=headless_mode
            )

            # Create initial context, passing proxy again (good practice)
            logger.info("Creating browser context...")
            context = browser.new_context(
                proxy=proxy_config,
                ignore_https_errors=True # Often helpful with proxies/IP checking sites
            )
            page = context.new_page()

            # Track usage of current IP
            start_time = time.time()
            request_count = 0

            logger.info("Starting request loop (Press Ctrl+C to stop)...")
            while True:
                current_ip = "Unknown" # Default in case of failure
                try:
                    # Check if we need to rotate IP by closing/reopening context
                    # Note: For IPRoyal sticky sessions, this might not force a *new* IP immediately
                    # unless the sticky session time also expires. Simple context closing primarily
                    # cleans the browser state (cookies, etc.). Check IPRoyal docs for forced rotation.
                    if (request_count >= max_requests_per_ip or
                        (max_time_per_ip > 0 and time.time() - start_time >= max_time_per_ip)):
                        logger.info(f"Rotation condition met (Requests: {request_count}/{max_requests_per_ip}, Time: {time.time() - start_time:.1f}/{max_time_per_ip}s). Recreating context...")
                        page.close()
                        context.close()
                        context = browser.new_context(proxy=proxy_config, ignore_https_errors=True)
                        page = context.new_page()
                        start_time = time.time()
                        request_count = 0
                        logger.info("Context recreated.")

                    logger.info(f"--- Request {request_count + 1} ---")

                    # Get IP address using a reliable service
                    logger.debug("Navigating to icanhazip.com...")
                    page.goto("https://ipv4.icanhazip.com", timeout=20000)
                    # Simpler content extraction
                    current_ip = page.locator('pre').text_content().strip()
                    logger.info(f"Current Exit IP: {current_ip}")

                    # Get detailed IP info (optional, can add delay/complexity)
                    # logger.debug("Navigating to ipapi.co...")
                    # response = page.goto("https://ipapi.co/json/", timeout=20000)
                    # ip_info = response.json()
                    # logger.info("IP Details:")
                    # logger.info(f"  City: {ip_info.get('city', 'N/A')}")
                    # logger.info(f"  Region: {ip_info.get('region', 'N/A')}")
                    # logger.info(f"  Country: {ip_info.get('country_name', 'N/A')}")
                    # logger.info(f"  Organization: {ip_info.get('org', 'N/A')}")

                    request_count += 1

                except PlaywrightError as e:
                     logger.error(f"Playwright error during request {request_count + 1} (IP: {current_ip}): {e}")
                     # Decide how to handle errors: break, continue, screenshot?
                     try:
                         page.screenshot(path=f"proxy_test_error_{request_count+1}.png")
                         logger.info("Saved error screenshot.")
                     except Exception as ss_err:
                         logger.error(f"Failed to save error screenshot: {ss_err}")
                     # Maybe force context rotation on error?
                     request_count = max_requests_per_ip # Force rotation on next loop

                except Exception as e:
                     logger.error(f"Unexpected error during request {request_count + 1} (IP: {current_ip}): {e}", exc_info=True)
                     request_count = max_requests_per_ip # Force rotation

                finally:
                     # Wait between requests, even after errors before potentially rotating
                     logger.debug(f"Sleeping for {request_delay:.1f} seconds...")
                     time.sleep(request_delay)

        except KeyboardInterrupt:
            logger.info("Ctrl+C detected. Stopping...")
        except Exception as e:
            logger.error(f"An uncaught error occurred in the main loop: {e}", exc_info=True)
        finally:
            logger.info("Closing browser...")
            if context:
                 context.close()
            if browser:
                browser.close()
            logger.info("--- Proxy Scraping Test Finished ---")

if __name__ == "__main__":
    scrape_with_iproyal_proxy()
