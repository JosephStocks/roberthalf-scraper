from playwright.sync_api import sync_playwright, TimeoutError
import json
from datetime import datetime
import time
import re
import os
from bs4 import BeautifulSoup # Import BeautifulSoup
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- Helper Functions ---

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

def clean_text(text):
    """Clean up text by removing extra whitespace and newlines."""
    if text:
        # Remove potential HTML entities that might linger
        text = re.sub(r'&[a-zA-Z]+;', ' ', text) 
        text = ' '.join(text.split())
        return text.strip()
    return None

def format_salary(min_s, max_s, currency, period):
    """Formats salary information from attributes."""
    try:
        min_f = float(min_s) if min_s else 0.0
        max_f = float(max_s) if max_s else 0.0

        if min_f == 0.0 and max_f == 0.0:
            return "DOE" # Determine On Experience/Not Specified
        
        period_str = f" / {period}" if period else ""
        currency_symbol = currency # Keep as code for now, could map later if needed (e.g., USD -> $)
        
        if min_f > 0.0 and max_f > 0.0 and min_f != max_f:
            return f"{min_f:.2f} - {max_f:.2f} {currency_symbol}{period_str}"
        elif max_f > 0.0:
             return f"{max_f:.2f} {currency_symbol}{period_str}" # Or Up to max_f
        elif min_f > 0.0:
             return f"{min_f:.2f} {currency_symbol}{period_str}" # Or Starting at min_f
        else:
             return "DOE" # Fallback
             
    except (ValueError, TypeError):
        return "DOE" # Error during conversion

def parse_iso_date(date_str):
    """Parses ISO 8601 date string and returns a readable format or original."""
    if not date_str:
        return None
    try:
        # Handle the 'Z' for UTC timezone
        if date_str.endswith('Z'):
            date_str = date_str[:-1] + '+00:00'
        dt_obj = datetime.fromisoformat(date_str)
        # Return in a simpler format, or keep as ISO string if preferred
        # return dt_obj.strftime('%Y-%m-%d') 
        return dt_obj.isoformat() # Keep original ISO format for data fidelity
    except ValueError:
        return date_str # Return original if parsing fails

def clean_html_description(html_content):
    """Extracts plain text from HTML content using BeautifulSoup."""
    if not html_content:
        return None
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        # Get text and clean it
        text = soup.get_text(separator=' ', strip=True)
        return clean_text(text)
    except Exception as e:
        print(f"Error cleaning HTML: {e}")
        # Fallback to basic regex cleaning if BS fails
        text = re.sub('<[^>]+>', ' ', html_content)
        return clean_text(text)

def is_valid_job(job_data):
    """Basic check if essential job data (title, url) exists."""
    return bool(job_data.get("title") and job_data.get("url"))

def handle_popups(page):
    """Handle various popups that might appear on the page"""
    try:
        # Grant geolocation permission implicitly via context
        
        # Wait briefly for cookie notice and try to click the close button
        print("Looking for cookie notice...")
        # Using a more specific selector if possible, based on typical cookie banners
        # If the exact selector isn't known, role 'button' with name 'Close' is a good guess
        close_button = page.locator('button:has-text("Close"), button[aria-label*="Close"], button[title*="Close"]').first 
        
        try:
             # Wait max 5 seconds for the button
            close_button.wait_for(state="visible", timeout=5000) 
            print("Closing cookie notice...")
            close_button.click()
            page.wait_for_load_state("networkidle", timeout=5000) # Wait after clicking
        except TimeoutError:
            print("Cookie notice close button not found or timed out.")
            
        # Add handling for other potential popups if they appear during testing
            
    except Exception as e:
        print(f"Error handling popups: {e}")

def login(page, username, password):
    """Handle the login process."""
    try:
        # Navigate to login page
        login_url = "https://online.roberthalf.com/s/login?app=0sp3w000001UJH5&c=US&d=en_US&language=en_US&redirect=false"
        print(f"Navigating to login page: {login_url}")
        page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
        
        # Wait for username field and type username
        username_field = page.locator('[data-id="username"] input')
        username_field.wait_for(state="visible", timeout=10000)
        username_field.fill(username)
        print("Username entered")
        
        # Wait for password field and type password
        password_field = page.locator('[data-id="password"] input')
        password_field.wait_for(state="visible", timeout=10000)
        password_field.fill(password)
        print("Password entered")
        
        # Click sign in button
        sign_in_button = page.locator('rhcl-button[data-id="signIn"]')
        sign_in_button.wait_for(state="visible", timeout=10000)
        sign_in_button.click()
        print("Sign in button clicked")
        
        # Wait for navigation after login
        page.wait_for_load_state("networkidle", timeout=30000)
        print("Login navigation complete")
        
        # Basic check if login was successful - this may need to be adjusted
        if "login" in page.url.lower():
            print("Still on login page - login may have failed")
            return False
            
        return True
        
    except Exception as e:
        print(f"Error during login: {e}")
        return False

# --- Main Scraping Function ---

def scrape_roberthalf_jobs():
    # Get credentials from .env file
    username = os.getenv('ROBERTHALF_USERNAME')
    password = os.getenv('ROBERTHALF_PASSWORD')
    
    if not username or not password:
        raise ValueError("Please set ROBERTHALF_USERNAME and ROBERTHALF_PASSWORD in your .env file")

    with sync_playwright() as p:
        # Get proxy and browser configurations
        proxy_config = get_proxy_config()
        
        # Browser configuration
        viewport_width = int(os.getenv('VIEWPORT_WIDTH', '1920'))
        viewport_height = int(os.getenv('VIEWPORT_HEIGHT', '1080'))
        user_agent = os.getenv('USER_AGENT', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36')

        browser = p.chromium.launch(
            proxy=proxy_config,
            headless=False  # Start with False for debugging
        )
        
        context = None # Initialize context to None
        try:
            context = browser.new_context(
                proxy=proxy_config,
                viewport={'width': viewport_width, 'height': viewport_height},
                user_agent=user_agent
            )
            # Grant permissions
            context.grant_permissions(['geolocation'])
            
            page = context.new_page()
            
            # Perform login first
            if not login(page, username, password):
                raise Exception("Login failed")
            print("Login successful")
            
            search_url = "https://www.roberthalf.com/us/en/jobs?city=Dallas&lobid=RHT"
            print(f"Navigating to: {search_url}")
            
            try:
                 page.goto(search_url, wait_until="domcontentloaded", timeout=60000) # Increased timeout
                 print("Initial page load complete (DOM loaded).")
            except TimeoutError:
                 print("Timeout during initial page navigation.")
                 raise # Re-raise the exception to stop execution
            
            handle_popups(page) # Handle popups early

            # Wait specifically for the job cards to appear in the main list area
            job_card_selector = "div.col-md-5.col-lg-5 rhcl-job-card" 
            print(f"Waiting for job cards using selector: '{job_card_selector}'...")
            try:
                 page.wait_for_selector(job_card_selector, state="visible", timeout=30000) # Wait up to 30s for cards
                 print("Job cards are visible.")
            except TimeoutError:
                 print(f"Timeout waiting for job cards ('{job_card_selector}'). Page might not have loaded correctly or structure changed.")
                 # Capture page content for debugging
                 content = page.content()
                 with open("debug_page_content.html", "w", encoding="utf-8") as f:
                     f.write(content)
                 print("Saved page content to debug_page_content.html")
                 raise Exception("Could not find job listings container.")
                 
            # Wait a bit longer for network activity to settle after cards appear
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
                print("Network is idle.")
            except TimeoutError:
                print("Network did not become idle, proceeding anyway.")

            # Now query for the job cards
            job_cards = page.query_selector_all(job_card_selector)
            print(f"Found {len(job_cards)} potential job cards.")

            if not job_cards:
                raise Exception("No job cards found after waiting.")

            jobs = []
            for i, card in enumerate(job_cards, 1):
                job_data = {}
                try:
                    # Extract data directly from attributes
                    job_data["title"] = clean_text(card.get_attribute("headline"))
                    job_data["location"] = clean_text(card.get_attribute("location"))
                    job_data["url"] = card.get_attribute("destination") # Already includes domain
                    job_data["job_type"] = clean_text(card.get_attribute("type"))
                    
                    salary_min = card.get_attribute("salary-min")
                    salary_max = card.get_attribute("salary-max")
                    salary_curr = card.get_attribute("salary-currency")
                    salary_per = card.get_attribute("salary-period")
                    job_data["pay_rate"] = format_salary(salary_min, salary_max, salary_curr, salary_per)
                    
                    job_data["posted_date"] = parse_iso_date(card.get_attribute("date"))
                    
                    # Get HTML description and clean it
                    html_desc = card.get_attribute("copy") 
                    job_data["description"] = clean_html_description(html_desc)
                    
                    # Add Job ID if needed
                    job_data["job_id"] = card.get_attribute("job-id") 

                    if is_valid_job(job_data):
                        jobs.append(job_data)
                        print(f"Scraped job {i}/{len(job_cards)}: {job_data['title']}")
                    else:
                        print(f"Skipping invalid job card {i}: Missing title or URL. Data: {job_data}")

                except Exception as e:
                    # Log the error but try to continue with the next card
                    print(f"Error processing job card {i}: {e}")
                    # Optionally log the problematic card's outer HTML
                    # print(f"Card HTML: {card.evaluate('el => el.outerHTML')}") 
                    continue # Move to the next card

            # Save results
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = f"roberthalf_jobs_{timestamp}.json"
            
            results_data = {
                "jobs": jobs,
                "timestamp": timestamp,
                "total_jobs": len(jobs),
                "search_location": "Dallas",
                "search_url": search_url,
                "status": "Completed"
            }

            with open(output_file, "w", encoding='utf-8') as f: # Ensure utf-8 encoding
                json.dump(results_data, f, indent=2, ensure_ascii=False)
            
            print(f"\nScraped {len(jobs)} jobs. Results saved to {output_file}")
            
            input("\nPress Enter to close the browser...") # Keep for debugging
            
        except Exception as e:
            print(f"\nAn error occurred during scraping: {e}")
            # Attempt to save partial results if any jobs were collected
            if 'jobs' in locals() and jobs:
                 timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_error")
                 output_file = f"roberthalf_jobs_{timestamp}.json"
                 error_data = {
                     "jobs": jobs,
                     "timestamp": timestamp,
                     "total_jobs": len(jobs),
                     "search_location": "Dallas",
                     "search_url": search_url if 'search_url' in locals() else 'N/A',
                     "status": f"Error occurred: {e}"
                 }
                 with open(output_file, "w", encoding='utf-8') as f:
                     json.dump(error_data, f, indent=2, ensure_ascii=False)
                 print(f"Saved partial results ({len(jobs)} jobs) to {output_file}")
            # Try saving page source on error if page object exists
            if 'page' in locals():
                 try:
                     content = page.content()
                     with open("error_page_content.html", "w", encoding="utf-8") as f:
                         f.write(content)
                     print("Saved page content on error to error_page_content.html")
                 except Exception as save_err:
                     print(f"Could not save page content on error: {save_err}")
                     
        finally:
             if context:
                 context.close() # Close context first
             browser.close()
             print("Browser closed.")

if __name__ == "__main__":
    scrape_roberthalf_jobs()