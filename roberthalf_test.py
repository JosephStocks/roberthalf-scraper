from playwright.sync_api import sync_playwright

from config_loader import load_test_config
from utils import get_proxy_config

# Load test configuration
load_test_config()


def test_roberthalf_page():
    with sync_playwright() as p:
        # Get proxy configuration
        proxy_config = get_proxy_config()

        # Launch browser with proxy configuration
        browser = p.chromium.launch(
            proxy=proxy_config,
            headless=False,  # Set to False to see the browser
        )

        try:
            # Create context with proxy
            context = browser.new_context(proxy=proxy_config)
            page = context.new_page()

            # Navigate to Robert Half jobs page
            print("Navigating to Robert Half jobs page...")
            page.goto("https://www.roberthalf.com/us/en/jobs?city=Dallas&lobid=RHT")

            # Wait for the page to load
            page.wait_for_load_state("networkidle")

            # Take a snapshot of the page structure
            print("\nTaking accessibility snapshot...")
            snapshot = page.accessibility.snapshot()
            print("Page structure:", snapshot)

            # Take a screenshot
            print("\nTaking screenshot...")
            page.screenshot(path="roberthalf_page.jpg")

            # Wait for user input to keep browser open
            input("\nPress Enter to close the browser...")

        except Exception as e:
            print(f"An error occurred: {e}")
        finally:
            browser.close()


if __name__ == "__main__":
    test_roberthalf_page()
