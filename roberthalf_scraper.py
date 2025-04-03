import random
from playwright.sync_api import sync_playwright, TimeoutError
import json
from datetime import datetime
import time
import requests
from bs4 import BeautifulSoup
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

def login_and_get_cookies(username, password):
    """Handle the login process and return required cookies."""
    with sync_playwright() as p:
        # Browser configuration
        user_agent = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36'
        proxy_config = get_proxy_config()
        
        browser = p.chromium.launch(
            proxy=proxy_config,
            headless=True  # Run headless since we just need cookies
        )
        
        try:
            context = browser.new_context(
                proxy=proxy_config,
                viewport={'width': 1920, 'height': 1080},
                user_agent=user_agent
            )
            
            page = context.new_page()
            
            # Navigate to login page
            login_url = "https://online.roberthalf.com/s/login?app=0sp3w000001UJH5&c=US&d=en_US&language=en_US&redirect=false"
            print("Navigating to login page...")
            page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
            
            # Login
            username_field = page.locator('[data-id="username"] input')
            username_field.wait_for(state="visible", timeout=10000)
            username_field.fill(username)
            
            password_field = page.locator('[data-id="password"] input')
            password_field.wait_for(state="visible", timeout=10000)
            password_field.fill(password)
            
            sign_in_button = page.locator('rhcl-button[data-id="signIn"]')
            sign_in_button.wait_for(state="visible", timeout=10000)
            sign_in_button.click()
            
            # Wait for navigation and cookies to be set
            page.wait_for_load_state("networkidle", timeout=30000)
            
            # Get all cookies
            cookies = context.cookies()
            
            # Convert cookies to dictionary format for requests
            cookie_dict = {cookie['name']: cookie['value'] for cookie in cookies}
            
            return cookie_dict, user_agent
            
        except Exception as e:
            print(f"Error during login: {e}")
            return None, None
        finally:
            browser.close()

def fetch_jobs(cookies, user_agent, page_number=1):
    """Fetch jobs using the API directly."""
    url = 'https://www.roberthalf.com/bin/jobSearchServlet'
    
    headers = {
        'accept': 'application/json, text/plain, */*',
        'accept-language': 'en-US,en;q=0.9',
        'content-type': 'application/json',
        'origin': 'https://www.roberthalf.com',
        'referer': 'https://www.roberthalf.com/us/en/jobs',
        'user-agent': user_agent
    }
    
    payload = {
        "country": "us",
        "keywords": "",
        "location": "",
        "distance": "50",
        "remote": "No",
        "remoteText": "",
        "languagecodes": [],
        "source": ["Salesforce"],
        "city": [],
        "emptype": [],
        "lobid": ["RHT"],
        "jobtype": "",
        "postedwithin": "PAST_24_HOURS",
        "timetype": "",
        "pagesize": 25,
        "pagenumber": page_number,
        "sortby": "RELEVANCE_DESC",
        "mode": "",
        "payratemin": 0,
        "includedoe": ""
    }
    
    try:
        response = requests.post(url, headers=headers, cookies=cookies, json=payload)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching jobs: {e}")
        return None

def filter_texas_jobs(jobs):
    """Filter jobs to only include Texas positions."""
    return [job for job in jobs if job.get('stateprovince') == 'TX']

def save_jobs(jobs, filename):
    """Save jobs to a JSON file."""
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(jobs, f, indent=2, ensure_ascii=False)

def scrape_roberthalf_jobs():
    # Get credentials from .env file
    username = os.getenv('ROBERTHALF_USERNAME')
    password = os.getenv('ROBERTHALF_PASSWORD')
    
    if not username or not password:
        raise ValueError("Please set ROBERTHALF_USERNAME and ROBERTHALF_PASSWORD in your .env file")

    # Login and get cookies
    print("Logging in to get cookies...")
    cookies, user_agent = login_and_get_cookies(username, password)
    
    if not cookies:
        print("Failed to obtain cookies. Exiting.")
        return

    all_texas_jobs = []
    page_number = 1
    total_jobs = None
    
    while True:
        print(f"Fetching page {page_number}...")
        response_data = fetch_jobs(cookies, user_agent, page_number)
        
        if not response_data:
            print("Failed to fetch jobs. Exiting.")
            break
            
        if total_jobs is None:
            total_jobs = int(response_data.get('found', 0))
            print(f"Total jobs found: {total_jobs}")
        
        # Get jobs from response and filter for Texas
        jobs = response_data.get('jobs', [])
        texas_jobs = filter_texas_jobs(jobs)
        all_texas_jobs.extend(texas_jobs)
        
        print(f"Found {len(texas_jobs)} Texas jobs on page {page_number}")
        
        # Check if we need to fetch more pages
        if len(jobs) < 25:  # Less than page size means last page
            break
            
        page_number += 1
        time.sleep(random.uniform(5, 15))  # Be nice to the server
    
    # Save results
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = f"roberthalf_texas_jobs_{timestamp}.json"
    
    results_data = {
        "jobs": all_texas_jobs,
        "timestamp": timestamp,
        "total_texas_jobs": len(all_texas_jobs),
        "total_jobs_found": total_jobs,
        "status": "Completed"
    }
    
    save_jobs(results_data, output_file)
    print(f"\nScraped {len(all_texas_jobs)} Texas jobs. Results saved to {output_file}")

if __name__ == "__main__":
    scrape_roberthalf_jobs()