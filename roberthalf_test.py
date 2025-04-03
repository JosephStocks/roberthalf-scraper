from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
import os

# Load environment variables from .env file
load_dotenv()

def get_proxy_config():
    """
    Creates proxy configuration based on environment variables.
    Returns None if proxy is disabled or not properly configured.
    """
    # Check if proxy is enabled
    use_proxy = os.getenv('USE_PROXY', 'false').lower() == 'true'
    if not use_proxy:
        print("Proxy is disabled")
        return None

    # Get proxy configuration
    proxy_server = os.getenv('PROXY_SERVER')
    proxy_auth = os.getenv('PROXY_AUTH')
    proxy_bypass = os.getenv('PROXY_BYPASS', '*.iproyal.com')

    # Validate proxy configuration
    if not proxy_server or not proxy_auth:
        print("Proxy configuration is incomplete")
        return None

    try:
        username, password = proxy_auth.split(':')
        return {
            "server": f"https://{proxy_server}",
            "username": username,
            "password": password,
            "bypass": proxy_bypass
        }
    except Exception as e:
        print(f"Error creating proxy configuration: {e}")
        return None

def test_roberthalf_page():
    with sync_playwright() as p:
        # Get proxy configuration
        proxy_config = get_proxy_config()
        
        # Launch browser with proxy configuration
        browser = p.chromium.launch(
            proxy=proxy_config,
            headless=False  # Set to False to see the browser
        )
        
        try:
            # Create context with proxy
            context = browser.new_context(proxy=proxy_config)
            page = context.new_page()
            
            # Navigate to Robert Half jobs page
            print("Navigating to Robert Half jobs page...")
            page.goto("https://www.roberthalf.com/us/en/jobs?city=Dallas&lobid=RHT")
            
            # Wait for the page to load
            page.wait_for_load_state('networkidle')
            
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