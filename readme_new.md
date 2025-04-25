# Job Scraper

A modular job scraping system that supports multiple job sites, including Robert Half and Keurig Dr Pepper.

## Features

- **Multi-site support**: Scrape jobs from different sources with a unified interface
- **Object-oriented design**: Easily extend with new job sources
- **Session management**: Save and reuse browser sessions to reduce login frequency
- **Job filtering**: Filter jobs by location, keywords, and more
- **Duplicate detection**: Track previously seen jobs to identify new listings
- **HTML reports**: Generate detailed reports for each source and a combined view
- **Proxy support**: Use proxies to avoid IP blocks
- **AI job matching**: (Optional) Score jobs against candidate profiles using GPT models

## Installation

### Requirements

- Python 3.10+
- Playwright (for browser automation)
- Other dependencies in `requirements.txt`

### Steps

1. Clone the repository:

```bash
git clone https://github.com/yourusername/job-scraper.git
cd job-scraper
```

2. Create a virtual environment:

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

4. Install Playwright browsers:

```bash
playwright install
```

5. Copy the example configuration:

```bash
cp .env.example .env
```

6. Edit `.env` to add your credentials and preferences

## Configuration

The system uses a `.env` file for configuration. Here are the important settings:

### General Settings

```ini
# Logging and Output
LOG_LEVEL=INFO               # DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_DIR=logs                 # Directory for log files
OUTPUT_DIR=output            # Directory for output files
DOCS_DIR=docs                # Directory for HTML reports

# Session Management
SAVE_SESSION=true            # Save sessions between runs
SESSION_DIR=.session         # Directory for session files
SESSION_MAX_AGE_HOURS=12     # Maximum age of session before forcing refresh

# Browser Settings
HEADLESS_BROWSER=true        # Run browser in headless mode
ROTATE_USER_AGENT=false      # Use random user agents
DEFAULT_USER_AGENT=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36
BROWSER_TIMEOUT_MS=60000     # Browser operation timeout (milliseconds)

# Request Settings
REQUEST_DELAY_SECONDS=2.0    # Delay between API requests (Robert Half)
PAGE_DELAY_MIN=5.0           # Minimum delay between page navigations
PAGE_DELAY_MAX=15.0          # Maximum delay between page navigations
MAX_RETRIES=3                # Maximum retries for failed requests
REQUEST_TIMEOUT_SECONDS=30   # HTTP request timeout (seconds)

# Proxy Settings (Optional)
USE_PROXY=false              # Set to true to enable proxy
PROXY_SERVER=your_proxy_server:port # e.g., http://user:pass@host:port or socks5://host:port
PROXY_AUTH=username:password # Optional, if required by proxy server (use if not in server URL)
PROXY_BYPASS=localhost,127.0.0.1 # Optional, comma-separated list of hosts to bypass proxy

# Source Enable/Disable
ROBERTHALF_ENABLED=true      # Enable Robert Half scraper (Default: true)
KDP_ENABLED=false            # Enable Keurig Dr Pepper scraper (Default: false)
```

### Robert Half Settings

```ini
ROBERTHALF_USERNAME=your_email@example.com
ROBERTHALF_PASSWORD=your_password
FILTER_STATE=TX              # State code to filter jobs by (e.g., TX, CA)
JOB_POST_PERIOD=PAST_24_HOURS # How recent jobs should be (e.g., PAST_7_DAYS, PAST_30_DAYS)
```

### Keurig Dr Pepper Settings

```ini
KDP_USERNAME=              # Usually not needed for searching
KDP_PASSWORD=
KDP_LOCATION="Frisco, TX"    # Location filter (e.g., "Essex Junction, VT", "Remote")
KDP_KEYWORDS="Supply Chain"  # Keyword filter
```

### AI Matching Settings (Optional)

```ini
MATCHING_ENABLED=false       # Enable AI job matching
OPENAI_API_KEY=your_openai_api_key
CANDIDATE_PROFILE_PATH=candidate_profile.json # Path to candidate profile
MATCHING_MODEL_TIER1=gpt-4o-mini
MATCHING_THRESHOLD_TIER1=60
MATCHING_MODEL_TIER2=gpt-4o-mini # Changed from 4.1-mini as it may not exist
MATCHING_THRESHOLD_FINAL=75
```

## Usage

Run the scraper from the command line:

```bash
# Run with default settings from .env
uv run python scrape_jobs.py

# Run with a specific config file and log level
uv run python scrape_jobs.py --config my_config.env --log-level DEBUG

# Enable KDP scraper for this run (overrides .env setting)
uv run python scrape_jobs.py --enable-kdp

# Disable Robert Half for this run
uv run python scrape_jobs.py --disable-robert-half

# Analyze all jobs (not just new ones)
uv run python scrape_jobs.py --analyze-all

# Run in test mode (may trigger notifications even without new jobs)
uv run python scrape_jobs.py --test-mode

# Display help
uv run python scrape_jobs.py --help
```

Reports will be generated in the `docs` directory (or the directory specified by `DOCS_DIR` / `--output-dir`).
Logs will be stored in the `logs` directory.