from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
import os
import time
import json

# Load environment variables
load_dotenv()

def scrape_with_iproyal_proxy():
    with sync_playwright() as p:
        # Get proxy configuration from environment variables
        proxy_server = os.getenv('PROXY_SERVER')
        proxy_auth = os.getenv('PROXY_AUTH')
        proxy_bypass = os.getenv('PROXY_BYPASS', '*.iproyal.com')  # Default to *.iproyal.com if not set
        
        if not proxy_server or not proxy_auth:
            raise ValueError("Proxy configuration not found in environment variables. Please check your .env file.")
        
        # Split proxy auth into username and password
        username, password = proxy_auth.split(':')
        
        # Get optional configuration from environment variables with defaults
        max_requests_per_ip = int(os.getenv('MAX_REQUESTS_PER_IP', '5'))
        max_time_per_ip = int(os.getenv('MAX_TIME_PER_IP', '30'))  # seconds
        request_delay = int(os.getenv('REQUEST_DELAY', '2'))
        
        # Proxy configuration dictionary used in multiple places
        proxy_config = {
            "server": f"https://{proxy_server}",
            "username": username,
            "password": password,
            "bypass": proxy_bypass
        }
        
        # Launch browser with proxy configuration
        browser = p.chromium.launch(
            proxy=proxy_config,
            headless=False  # Set to True in production
        )
        
        try:
            # Create initial context
            context = browser.new_context(proxy=proxy_config)
            page = context.new_page()
            
            # Track usage of current IP
            start_time = time.time()
            request_count = 0
            
            while True:
                # Check if we need to rotate IP
                if (request_count >= max_requests_per_ip or 
                    time.time() - start_time >= max_time_per_ip):
                    print("\nRotating IP...")
                    context.close()
                    context = browser.new_context(proxy=proxy_config)
                    page = context.new_page()
                    start_time = time.time()
                    request_count = 0
                
                print(f"\nRequest {request_count + 1} with current IP:")
                
                # Get IP address
                page.goto("https://ipv4.icanhazip.com")
                ip_address = page.content().strip()
                ip_address = ip_address.replace('<html><head><meta name="color-scheme" content="light dark"></head><body><pre style="word-wrap: break-word; white-space: pre-wrap;">', '').replace('</pre></body></html>', '').strip()
                print(f"Current IP: {ip_address}")
                
                # Get detailed IP info
                response = page.goto("https://ipapi.co/json/")
                ip_info = response.json()
                
                print("IP Details:")
                print(f"  City: {ip_info.get('city', 'N/A')}")
                print(f"  Region: {ip_info.get('region', 'N/A')}")
                print(f"  Country: {ip_info.get('country_name', 'N/A')}")
                print(f"  Organization: {ip_info.get('org', 'N/A')}")
                print(f"  Timezone: {ip_info.get('timezone', 'N/A')}")
                
                request_count += 1
                time.sleep(request_delay)  # Wait between requests
            
        except KeyboardInterrupt:
            print("\nStopping...")
        except Exception as e:
            print(f"An error occurred: {e}")
        finally:
            browser.close()

# Run the function
scrape_with_iproyal_proxy()
