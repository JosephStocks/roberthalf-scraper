# Robert Half Job Scraper

## Description

This Python script scrapes job listings from Robert Half's website. It automates the login process using Playwright, maintains session persistence, and then directly calls the internal job search API (`/bin/jobSearchServlet`) to retrieve job postings based on configured filters (like state and posting period). The script fetches both state-specific and remote US jobs, combining them into a single result set. The results are saved to a JSON file, an HTML report is generated and pushed to Git, and notifications are sent via Pushover. All timestamps in the HTML report are displayed in Central Time (CST/CDT).

## Features

*   **Automated Login:** Uses Playwright to handle the Robert Half login process.
*   **Session Persistence:** Saves and reloads login session data (cookies and user agent) to minimize repeated logins. Session validity is checked and refreshed if expired or invalid.
*   **Direct API Interaction:** Fetches job data efficiently by calling the internal API endpoint directly after authentication.
*   **Smart Job Filtering:**
    *   Filters for jobs in the specified state (`FILTER_STATE`)
    *   Includes all US-based remote jobs regardless of state
    *   Combines and deduplicates results
    *   Sorts jobs by posted date (most recent first)
*   **Timezone Handling:** All timestamps in the HTML report are converted to Central Time (CST/CDT)
*   **Push Notifications:** Sends detailed job notifications via Pushover, including:
    *   Separate counts for state-specific and remote jobs
    *   Location details (Remote or City, State)
    *   Salary information when available
    *   A link to the generated HTML report hosted on a publicly accessible URL (configured via `GITHUB_PAGES_URL`).
*   **Configurable Filtering:** Allows filtering jobs by State (`FILTER_STATE`) and Job Posting Period (`JOB_POST_PERIOD`) via environment variables.
*   **Pagination Handling:** Automatically iterates through all pages of job results from the API for both local and remote jobs.
*   **Proxy Support:** Configurable support for using HTTP proxies (including authentication).
*   **User Agent Rotation:** Option to rotate user agents for requests.
*   **Retry Logic:** Implements exponential backoff for failed API requests.
*   **Human-like Delays:** Incorporates random delays to mimic human browsing behavior.
*   **Detailed Logging:** Logs activities and errors to both console and `logs/scraper.log`.
*   **JSON Output:** Saves scraped and filtered job data to a timestamped JSON file in the `output/` directory.
*   **HTML Report Generation:** Creates a user-friendly HTML report (`docs/jobs.html`) displaying jobs sorted by date with details and expandable descriptions.
*   **Automated Git Commit/Push:** Automatically adds, commits, and pushes the updated `docs/jobs.html` report to the Git repository. Supports authentication via `GITHUB_ACCESS_TOKEN` for HTTPS remotes, falling back to ambient authentication (SSH keys, credential helper) otherwise.

## Requirements

*   **Python:** >=3.13 (as specified in `pyproject.toml`)
*   **Dependencies:** Listed in `pyproject.toml` (install via `uv pip install .` or `pip install .`)
*   **Playwright Browsers:** (`playwright install`)
*   **Git:** Required for the automated commit/push feature. Must be installed and accessible in the system's PATH.
*   **Git Authentication:** For the automated push feature to work, *either*:
    *   Provide a `GITHUB_ACCESS_TOKEN` in the `.env` file for repositories using HTTPS remotes.
    *   *Or* ensure the environment where the script runs has pre-configured Git authentication (e.g., SSH keys authorized with GitHub, a Git credential helper configured).

## Installation

1.  **Clone the repository:**
    ```bash
    git clone <your-repository-url>
    cd scrape-roberthalf
    ```
2.  **Create a virtual environment (recommended):**
    ```bash
    python -m venv venv
    source venv/bin/activate # On Windows use `venv\Scripts\activate`
    ```
3.  **Install dependencies using `uv` (or `pip`):**
    ```bash
    # Recommended (uses uv.lock for exact versions)
    pip install uv
    uv pip install .

    # Alternative using pip directly (might install slightly different versions)
    # pip install .
    ```
4.  **Install Playwright browsers:**
    ```bash
    playwright install chromium # Or install all: playwright install
    ```
5.  **Set up configuration:**
    *   Copy the example environment file:
        ```bash
        cp .env.example .env
        ```
    *   Edit the `.env` file with your actual Robert Half credentials, Pushover keys, optional `GITHUB_ACCESS_TOKEN`, the `GITHUB_PAGES_URL` pointing to your hosted report, and desired settings. **Do not commit the `.env` file with your credentials.**
    *   Ensure your Git environment meets the authentication requirements mentioned above if using the push feature.

## Configuration

Configuration is managed via environment variables, typically stored in a `.env` file in the project root directory. See `.env.example` for all options.

**Key Variables:**

| Variable                  | Description                                                                                     | Default/Example Value          | Loaded By         | Used By               |
| :------------------------ | :---------------------------------------------------------------------------------------------- | :----------------------------- | :---------------- | :-------------------- |
| `ROBERTHALF_USERNAME`     | **Required.** Your Robert Half login email.                                                     | `your.email@example.com`       | `config_loader`   | `roberthalf_scraper`  |
| `ROBERTHALF_PASSWORD`     | **Required.** Your Robert Half login password.                                                  | `your_password_here`           | `config_loader`   | `roberthalf_scraper`  |
| `PUSHOVER_ENABLED`        | `true` to enable Pushover notifications, `false` to disable.                                  | `true`                         | `config_loader`   | `roberthalf_scraper`  |
| `PUSHOVER_TOKEN`          | **Required if enabled.** Your Pushover application token.                                     | `your_pushover_token`          | `config_loader`   | `pushnotify`          |
| `PUSHOVER_USER_KEY_JOE`   | **Required if enabled.** Pushover user key for 'Joe'.                                         | `your_pushover_user_key`       | `config_loader`   | `pushnotify`          |
| `PUSHOVER_USER_KEY_KATIE` | Optional. Pushover user key for 'Katie'.                                                      | `your_pushover_user_key_katie` | `config_loader`   | `pushnotify`          |
| `USE_PROXY`               | `true` to enable proxy usage, `false` to disable.                                             | `false`                        | `config_loader`   | `utils`               |
| `PROXY_SERVER`            | Proxy server address and port (e.g., `host:port`). Used if `USE_PROXY` is `true`.                 | `geo.iproyal.com:12321`        | `config_loader`   | `utils`               |
| `PROXY_AUTH`              | Proxy authentication in `username:password` format. Used if `USE_PROXY` is `true`.            | `username:password...`         | `config_loader`   | `utils`               |
| `PROXY_BYPASS`            | Optional comma-separated list of hosts to bypass the proxy for.                             | `*.iproyal.com`                | `config_loader`   | `utils`               |
| `SAVE_SESSION`            | `true` to save/load session data, `false` otherwise.                                          | `true`                         | `config_loader`   | `roberthalf_scraper`  |
| `SESSION_FILE`            | Filename for storing session data within `.session/`.                                           | `session_data.json`            | `config_loader`   | `roberthalf_scraper`  |
| `SESSION_MAX_AGE_HOURS`   | Max age of saved session data before forcing new login.                                         | `12`                           | `config_loader`   | `roberthalf_scraper`  |
| `FILTER_STATE`            | Two-letter state code to filter jobs (e.g., `TX`). Also includes all US remote jobs.            | `TX`                           | `config_loader`   | `roberthalf_scraper`  |
| `JOB_POST_PERIOD`         | Time period for job postings (e.g., `PAST_24_HOURS`, `PAST_3_DAYS`, `PAST_WEEK`, `ALL`).        | `PAST_24_HOURS`                | `config_loader`   | `roberthalf_scraper`  |
| `HEADLESS_BROWSER`        | `true` for headless browser, `false` for visible (debug).                                     | `true`                         | `config_loader`   | `roberthalf_scraper`  |
| `ROTATE_USER_AGENT`       | `true` to use random user agents, `false` to use `DEFAULT_USER_AGENT`.                        | `false`                        | `config_loader`   | `roberthalf_scraper`  |
| `DEFAULT_USER_AGENT`      | User agent if `ROTATE_USER_AGENT` is `false`.                                                 | `Mozilla/5.0...Chrome/134...`  | `config_loader`   | `roberthalf_scraper`  |
| `REQUEST_DELAY_SECONDS`   | Base delay between fetching subsequent pages (after page-specific delay).                       | `2`                            | `config_loader`   | `roberthalf_scraper`  |
| `PAGE_DELAY_MIN`          | Minimum delay between fetching subsequent pages.                                              | `5`                            | `config_loader`   | `roberthalf_scraper`  |
| `PAGE_DELAY_MAX`          | Maximum delay between fetching subsequent pages.                                              | `15`                           | `config_loader`   | `roberthalf_scraper`  |
| `MAX_RETRIES`             | Max retry attempts for failed API requests.                                                   | `3`                            | `config_loader`   | `roberthalf_scraper`  |
| `BROWSER_TIMEOUT_MS`      | Timeout for Playwright operations (milliseconds).                                             | `60000`                        | `config_loader`   | `roberthalf_scraper`  |
| `REQUEST_TIMEOUT_SECONDS` | Timeout for direct HTTP API requests (seconds).                                               | `30`                           | `config_loader`   | `roberthalf_scraper`  |
| `TEST_MODE`               | `true` to force notifications/Git push even if no jobs found (for testing).                   | `false`                        | `config_loader`   | `roberthalf_scraper`  |
| `LOG_LEVEL`               | Logging level (e.g., `DEBUG`, `INFO`, `WARNING`).                                               | `INFO`                         | `config_loader`   | `roberthalf_scraper`  |
| `GITHUB_ACCESS_TOKEN`     | **Optional.** GitHub Personal Access Token (Classic or Fine-Grained) for Git push authentication via HTTPS. If not provided, push relies on ambient auth (SSH keys, credential helper). | `your_github_access_token` | `config_loader`   | `roberthalf_scraper` |
| `GITHUB_PAGES_URL`        | **Required if PUSHOVER_ENABLED=true.** The public URL where the generated `docs/jobs.html` report will be hosted (e.g., your GitHub Pages site). Used in Pushover notifications. | `https://YOUR_USERNAME.github.io/YOUR_REPO_NAME/jobs.html` | `config_loader` | `roberthalf_scraper` |

*Note:* Variables like `IPROYAL_PROXY_SERVER` and `IPROYAL_PROXY_AUTH` in `.env.example` are helper variables used within the `.env` file itself to set `PROXY_SERVER` and `PROXY_AUTH`. They are not directly read by the Python scripts.

## Output Format

The script saves results to a JSON file in the `output/` directory with the following structure:
```json
{
    "jobs": [...],  // Array of job objects, sorted by posted date (newest first)
    "timestamp": "2024-03-27T10:30:00Z", // ISO 8601 UTC timestamp
    "total_[state]_jobs": 5, // e.g., total_tx_jobs
    "total_remote_jobs": 14,
    "total_jobs_found_in_period": 250, // Total reported by API across all pages/types
    "job_post_period_filter": "PAST_24_HOURS",
    "state_filter": "TX",
    "status": "Completed"
}
```

The script also generates an HTML report (`docs/jobs.html`) that displays the jobs in a user-friendly format with the following features:
- Jobs are sorted by posted date (newest first)
- All timestamps are displayed in Central Time (CST/CDT)
- Each job entry includes:
  - Job title with link to the full posting
  - Location (city/state or Remote)
  - Pay rate (if available)
  - Job ID
  - Posted date in CST/CDT
  - Expandable job description

This HTML file is automatically committed and pushed to the Git repository, making it suitable for hosting via GitHub Pages or similar services.

## Push Notifications

If enabled via `PUSHOVER_ENABLED=true`, the script sends notifications via Pushover with the following information:
- Number of new state-specific jobs found
- Number of new remote jobs found
- Details of up to 5 latest positions including:
  - Job title
  - Location (either "Remote" or "City, State")
  - Salary range and period (if available)
- A link (`url` parameter) pointing to the hosted `jobs.html` report (the URL is specified by the `GITHUB_PAGES_URL` environment variable).
- A custom title (`url_title`) for the link.

## Usage

Ensure your `.env` file is correctly configured, especially with your credentials, Pushover keys, and the `GITHUB_PAGES_URL`. If using the Git push feature, ensure Git is configured for pushing to your remote repository.

Run the script from the project's root directory using `uv` (or `python` if `uv` is not used):

```bash
# Recommended
uv run python roberthalf_scraper.py

# Alternative
# python roberthalf_scraper.py
```

*   Logs will be printed to the console and saved to `logs/scraper.log` (overwritten each run).
*   If successful, a JSON file named `roberthalf_[state]_jobs_[timestamp].json` (e.g., `roberthalf_tx_jobs_20240827_103000.json`) will be created in the `output/` directory.
*   The `docs/jobs.html` file will be generated or updated.
*   If new jobs are found (or `TEST_MODE=true`), the `docs/jobs.html` file will be committed and pushed to Git.
*   If `PUSHOVER_ENABLED=true` and new jobs are found (or `TEST_MODE=true`), a notification will be sent (provided `GITHUB_PAGES_URL` is also set correctly).
*   Session data (if enabled) will be stored in the `.session/` directory, using the filename specified by `SESSION_FILE`.

## Automated Scheduling (systemd)

For Linux systems using systemd, you can automate the scraper to run on a schedule using the provided user service and timer units. This runs the scraper every 2 hours between 7 AM and 9 PM.

1.  **Ensure Prerequisites:**
    *   Make sure the installation steps (cloning, dependencies, Playwright browsers, `.env` file with all required variables including `GITHUB_PAGES_URL`) are complete.
    *   The systemd units assume the script and its dependencies are runnable from the project's root directory (`/home/jstocks/PROJECTS/scrape-roberthalf` in the service file - **adjust this path if yours differs**).
    *   The service file uses `uv run`. Ensure `uv` is installed and accessible, or modify the `ExecStart` line to use `python` directly if preferred.
    *   Your user must be allowed to run long-running services (check with `loginctl show-user $USER | grep Linger` - if `no`, run `loginctl enable-linger $USER`).

2.  **Run the Installation Script:**
    This script copies the unit files to the correct user directory (`~/.config/systemd/user/`), reloads the systemd user daemon, and enables/starts the timer.
    ```bash
    chmod +x install_systemd_user.sh
    ./install_systemd_user.sh
    ```

3.  **Managing the Service/Timer:**
    *   Check Timer Status: `systemctl --user status roberthalf-scraper.timer`
    *   Check Last Service Run: `systemctl --user status roberthalf-scraper.service`
    *   View Logs: `journalctl --user -u roberthalf-scraper.service -f`
    *   List All User Timers: `systemctl --user list-timers`
    *   Stop Timer: `systemctl --user stop roberthalf-scraper.timer`
    *   Disable Timer: `systemctl --user disable roberthalf-scraper.timer`
    *   Re-enable and Start: `systemctl --user enable --now roberthalf-scraper.timer`

4.  **Uninstall:**
    ```bash
    systemctl --user disable --now roberthalf-scraper.timer
    rm ~/.config/systemd/user/roberthalf-scraper.service
    rm ~/.config/systemd/user/roberthalf-scraper.timer
    systemctl --user daemon-reload
    ```

## Key Script Components

*   **`roberthalf_scraper.py`:** The main executable script.
    *   `scrape_roberthalf_jobs()`: Orchestrates the entire process.
    *   `login_and_get_session()`: Handles Playwright login, returns cookies and UA.
    *   `load_session_data()` / `save_session_data()`: Manages session file I/O and expiry check.
    *   `validate_session()`: Checks if current session cookies are valid via API call.
    *   `get_or_refresh_session()`: Gets existing or triggers new login/save.
    *   `fetch_jobs()`: Makes the direct API request for a page of jobs.
    *   `fetch_with_retry()`: Wraps `fetch_jobs` with retry logic.
    *   `filter_jobs_by_state()`: Filters API response based on state/remote criteria.
    *   `save_job_results()`: Saves JSON, generates HTML, triggers Git push and notifications.
    *   `_generate_html_report()`: Creates the HTML content for `docs/jobs.html`.
    *   `_commit_and_push_report()`: Handles Git add, commit, and push operations.
*   **`config_loader.py`:** Loads configuration from `.env` files and environment variables, performs basic type conversion and validation.
*   **`utils.py`:** Contains utility functions, notably `get_proxy_config()` for parsing proxy settings from environment variables.
*   **`pushnotify.py`:** Handles sending notifications via the Pushover API.
*   **`.env.example` / `.env`:** Files for storing configuration variables (credentials, settings).
*   **`systemd/`:** Contains the `*.service` and `*.timer` files for automated scheduling.
*   **`docs/jobs.html`:** The generated HTML report (updated each run).
*   **`output/`:** Directory where timestamped JSON results are saved.
*   **`logs/`:** Directory where `scraper.log` is saved.
*   **`.session/`:** Directory where session data (`session_data.json`) is stored.

## Development Utilities

These scripts are included for testing specific functionalities during development:

*   **`proxy-scraping-test.py`:** Uses `config_loader` and `utils` to test the proxy configuration (`.env.test`) by making repeated requests to IP-checking websites and rotating the browser context. Useful for verifying proxy setup and rotation logic.
*   **`roberthalf_test.py`:** A simple Playwright script that navigates to a Robert Half job page using the proxy configuration from `.env.test`, takes a screenshot, and prints accessibility info. Useful for basic browser automation checks and verifying page structure.

## Limitations and Considerations

*   **Website/API Changes:** Relies on specific website login elements and API structure. Changes by Robert Half can break the scraper.
*   **Login Fragility:** Automated login can be detected or changed. Captchas or MFA would require significant updates.
*   **Rate Limiting/Blocking:** Excessive requests might lead to blocks. Delays and proxies are mitigation attempts.
*   **Session Validity:** Sessions can expire unexpectedly. Validation and refresh logic attempt to handle this.
*   **Error Handling:** While retries and basic error handling exist, complex network or API issues might require more robustness.
*   **Git Authentication:** The automated push feature requires proper authentication. If using an HTTPS remote, providing a `GITHUB_ACCESS_TOKEN` is recommended. If using SSH remotes or preferring not to use a token, the environment must have existing Git credentials configured (e.g., SSH key agent, credential helper). The script will attempt the push using the token method first if available and the remote is HTTPS; otherwise, it falls back to a standard `git push`.
*   **Pushover URL:** Ensure the `GITHUB_PAGES_URL` variable in your `.env` file points to the correct public URL where the `jobs.html` report is hosted.

## API Documentation: `/bin/jobSearchServlet`

*(This section remains unchanged from the original, as the API interaction logic itself was not modified)*

*   **Endpoint:** `https://www.roberthalf.com/bin/jobSearchServlet`
*   **HTTP Method:** `POST`
*   **Description:** Fetches job listings based on various search and filter criteria. Requires authentication via session cookies.
*   **Authentication:** Relies on session cookies obtained after a user successfully logs in through the Robert Half web portal. These cookies must be included in the request headers. The specific cookies required may vary, but examples observed include `apex__rememberMe`, `__cf_bm`, etc.
*   **Key Request Headers:**
    *   `accept: application/json, text/plain, */*`: Informs the server the client expects a JSON response.
    *   `content-type: application/json`: Specifies the request body format is JSON.
    *   `cookie: <session_cookies>`: **Crucial.** Contains the authentication cookies from the user's session. The script obtains these via Playwright login.
    *   `origin: https://www.roberthalf.com`: Standard header indicating the request origin.
    *   `referer: https://www.roberthalf.com/us/en/jobs...`: Indicates the page from which the request originates.
    *   `user-agent: <browser_user_agent>`: Identifies the client browser/script. The script uses a configured or rotated UA.
*   **Request Body (JSON Payload):**
    A JSON object containing filter parameters. Key parameters observed:

    | Parameter      | Type          | Description                                                                                                | Example from `curl` / Script Usage      |
    | :------------- | :------------ | :--------------------------------------------------------------------------------------------------------- | :-------------------------------------- |
    | `country`      | String        | Country code (e.g., 'us').                                                                                 | `"us"`                                  |
    | `keywords`     | String        | Job title, skill, or keyword search terms.                                                                 | `""` (Empty in script)                  |
    | `location`     | String        | General location search term (can be city, state, zip).                                                    | `""` (Empty in script)                  |
    | `distance`     | String        | Search radius around the location.                                                                         | `"50"` (Hardcoded in script)            |
    | `remote`       | String        | Filter for remote jobs ("yes", "No", ""). Script uses "yes" or "No".                                       | `"No"` or `"yes"` (Based on loop)       |
    | `source`       | List[String]  | Source system for jobs (e.g., Salesforce).                                                                 | `["Salesforce"]` (Hardcoded)            |
    | `city`         | List[String]  | List of specific cities to filter by. *Note: The script doesn't use this, filtering by state post-API call.* | `[]` (Empty in script)                  |
    | `lobid`        | List[String]  | Line of Business ID (e.g., "RHT" for Robert Half Technology). Likely important for scope.                | `["RHT"]` (Hardcoded)                   |
    | `postedwithin` | String        | Time frame for job posting date (maps to `JOB_POST_PERIOD`).                                               | `"PAST_24_HOURS"` (Configurable)        |
    | `pagesize`     | Integer       | Number of job results per page.                                                                            | `25` (Hardcoded)                        |
    | `pagenumber`   | Integer       | The page number of results to retrieve (1-based).                                                          | `1`, `2`, ... (Iterated by script)      |
    | `sortby`       | String        | Sorting criteria (e.g., `PUBLISHED_DATE_DESC`, `RELEVANCE_DESC`).                                          | `"PUBLISHED_DATE_DESC"` (Hardcoded)     |
    | `payratemin`   | Integer       | Minimum pay rate filter.                                                                                   | `0` (Hardcoded)                         |
    | `stateprovince`| String        | *Not observed in request payload, filtering done client-side by script based on response.*                 | N/A (Used for filtering response)       |

*   **Example Request (`curl`):**
    ```bash
    curl 'https://www.roberthalf.com/bin/jobSearchServlet' \
      -H 'accept: application/json, text/plain, */*' \
      # ... other headers (cookie, user-agent, referer, etc.) ...
      -H 'content-type: application/json' \
      --data-raw '{"country":"us","keywords":"","location":"","distance":"50","remote":"No","remoteText":"","languagecodes":[],"source":["Salesforce"],"city":[],"emptype":[],"lobid":["RHT"],"jobtype":"","postedwithin":"PAST_24_HOURS","timetype":"","pagesize":25,"pagenumber":1,"sortby":"PUBLISHED_DATE_DESC","mode":"","payratemin":0,"includedoe":""}'
    ```

*   **Example Successful Response (JSON):**
    The response is a JSON object containing metadata and the job listings.
    ```json
    {
        "aws_region": "us-west-2",
        "request_id": "da345274-2fda-4bb5-a69c-1a7040a6054f",
        "request_status": "SUCCESS",
        "request_message": "OK",
        "found": "54", // Total number of jobs matching the criteria FOR THIS REQUEST TYPE (local or remote)
        "facets": { ... }, // Data for refining search (counts by type, city, etc.)
        "google_request_id": "...",
        "jobs": [ // Array of job objects
            {
                "google_job_id": "...",
                "unique_job_number": "03720-0013168737-usen",
                "jobtitle": "Sr. Software Engineer - Backend",
                "description": "...", // HTML description
                "date_posted": "2025-03-25T14:59:14Z",
                "skills": "...", // HTML list of skills
                "lob_code": "RHT",
                "functional_role": "Sr. Software Engineer",
                "emptype": "Perm", // Employment type (Perm, Temp, Temp to Perm)
                "country": "US",
                "city": "King of Prussia",
                "stateprovince": "PA", // State code used for filtering
                "postalcode": "19406",
                "payrate_min": "150000.00",
                "payrate_max": "185000.00",
                "payrate_period": "Yearly",
                "salary_currency": "USD",
                "remote": "No", // "yes" or "No"
                "job_detail_url": "https://www.roberthalf.com/us/en/job/...",
                "source": "Salesforce",
                // ... other fields
            },
            // ... more job objects
        ]
    }
    ```
    **Key Response Fields:**
    *   `found`: String representing the total number of jobs found matching the criteria *for the current request type (local or remote)* across all pages. The script aggregates these.
    *   `facets`: An object containing counts for various filters (employment type, city, posted date), useful for UI refinements but not directly used by this script.
    *   `jobs`: An array containing the job listing objects for the current page.
    *   `jobs[].jobtitle`: The title of the job.
    *   `jobs[].description`: Job description, often contains HTML.
    *   `jobs[].city`, `jobs[].stateprovince`, `jobs[].postalcode`: Location details. `stateprovince` is used by the script for filtering.
    *   `jobs[].date_posted`: The date the job was posted (ISO 8601 format).
    *   `jobs[].payrate_min`, `jobs[].payrate_max`, `jobs[].payrate_period`: Salary/pay information, if available.
    *   `jobs[].job_detail_url`: Direct link to the job posting page.
    *   `jobs[].emptype`: Type of employment (Perm, Temp, etc.).
    *   `jobs[].remote`: Indicates if the job is remote ("yes" or "No").

*   **Error Handling:** The script checks for non-2xx HTTP status codes. Status codes like 401/403 likely indicate an invalid or expired session (triggering re-login attempt via validation). Invalid JSON responses also indicate potential session issues or API errors.
