# Robert Half Job Scraper

## Description

This Python script scrapes job listings from Robert Half's website. It automates the login process using Playwright, maintains session persistence, and then directly calls the internal job search API (`/bin/jobSearchServlet`) to retrieve job postings based on configured filters (like state and posting period). The results are saved to a JSON file.

## Features

*   **Automated Login:** Uses Playwright to handle the Robert Half login process.
*   **Session Persistence:** Saves and reloads login session data (cookies and user agent) to minimize repeated logins. Session validity is checked and refreshed if expired or invalid.
*   **Direct API Interaction:** Fetches job data efficiently by calling the internal API endpoint directly after authentication.
*   **Configurable Filtering:** Allows filtering jobs by State (`FILTER_STATE`) and Job Posting Period (`JOB_POST_PERIOD`) via environment variables.
*   **Pagination Handling:** Automatically iterates through all pages of job results from the API.
*   **Proxy Support:** Configurable support for using HTTP proxies (including authentication).
*   **User Agent Rotation:** Option to rotate user agents for requests.
*   **Retry Logic:** Implements exponential backoff for failed API requests.
*   **Human-like Delays:** Incorporates random delays to mimic human browsing behavior.
*   **Detailed Logging:** Logs activities and errors to both console and `scraper.log`.
*   **JSON Output:** Saves scraped and filtered job data to a timestamped JSON file.

## Requirements

*   **Python:** >=3.13 (as specified in `pyproject.toml`)
*   **Dependencies:** Listed in `pyproject.toml`:
    *   `playwright>=1.51.0`
    *   `python-dotenv>=1.1.0`
    *   `requests>=2.32.3`
*   **Playwright Browsers:** You need to install the browser binaries for Playwright (`playwright install`).

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
3.  **Install dependencies:**
    ```bash
    pip install .
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
    *   Edit the `.env` file with your actual Robert Half credentials and desired settings. **Do not commit the `.env` file with your credentials.**

## Configuration

Configuration is managed via environment variables, typically stored in a `.env` file in the project root directory. See `.env.example` for all options.

**Key Variables:**

| Variable                | Description                                                                                   | Default/Example Value        |
| :---------------------- | :-------------------------------------------------------------------------------------------- | :--------------------------- |
| `ROBERTHALF_USERNAME`   | **Required.** Your Robert Half login email.                                                   | `your.email@example.com`     |
| `ROBERTHALF_PASSWORD`   | **Required.** Your Robert Half login password.                                                | `your_password_here`         |
| `USE_PROXY`             | Set to `true` to enable proxy usage, `false` to disable.                                      | `false`                      |
| `PROXY_SERVER`          | Proxy server address and port (e.g., `host:port`).                                            | `geo.iproyal.com:12321`      |
| `PROXY_AUTH`            | Proxy authentication in `username:password` format.                                           | `username:password...`       |
| `PROXY_BYPASS`          | Comma-separated list of hosts to bypass the proxy for.                                        | `*.iproyal.com`              |
| `SAVE_SESSION`          | `true` to save/load session data, `false` otherwise.                                          | `true`                       |
| `SESSION_FILE`          | Filename for storing session data (cookies, user agent, timestamp).                           | `session_data.json`          |
| `SESSION_MAX_AGE_HOURS` | Maximum age of saved session data in hours before forcing a new login.                        | `12`                         |
| `FILTER_STATE`          | Two-letter state code to filter jobs (e.g., `TX`, `CA`, `PA`).                                | `TX`                         |
| `JOB_POST_PERIOD`       | Time period for job postings (e.g., `PAST_24_HOURS`, `PAST_3_DAYS`, `PAST_WEEK`, `ALL`).      | `PAST_24_HOURS`              |
| `HEADLESS_BROWSER`      | `true` to run Playwright browser without UI, `false` for visible browser (useful for debug).    | `true`                       |
| `ROTATE_USER_AGENT`     | `true` to use random user agents from a predefined list, `false` to use `DEFAULT_USER_AGENT`. | `false`                      |
| `DEFAULT_USER_AGENT`    | The user agent string to use if `ROTATE_USER_AGENT` is `false`.                               | `Mozilla/5.0...Chrome/134...`|
| `REQUEST_DELAY_SECONDS` | Base delay (in seconds) added between API requests (currently only used during retries).      | `2`                          |
| `PAGE_DELAY_MIN`        | Minimum delay (in seconds) between fetching subsequent pages of job results.                    | `5`                          |
| `PAGE_DELAY_MAX`        | Maximum delay (in seconds) between fetching subsequent pages of job results.                    | `15`                         |
| `MAX_RETRIES`           | Maximum number of retry attempts for failed API requests.                                     | `3`                          |
| `BROWSER_TIMEOUT_MS`    | Timeout for Playwright browser operations in milliseconds.                                    | `60000` (60 seconds)         |
| `REQUEST_TIMEOUT_SECONDS`| Timeout for direct HTTP API requests in seconds.                                              | `30`                         |

## Usage

Ensure your `.env` file is correctly configured, especially with your credentials.

Run the script from the project's root directory:

```bash
python roberthalf_scraper.py
```

*   Logs will be printed to the console and saved to `scraper.log` (overwritten each run).
*   If successful, a JSON file named `roberthalf_[state]_jobs_[timestamp].json` (e.g., `roberthalf_tx_jobs_20240827_103000.json`) will be created in the same directory, containing the scraped job data.
*   Session data (if enabled) will be stored in the file specified by `SESSION_FILE`.

## Key Script Components

*   **`login_and_get_session()`:** Handles the browser automation using Playwright to log into Robert Half and extract session cookies and the user agent used.
*   **`load_session_data()` / `save_session_data()`:** Manages reading and writing session information (cookies, UA, timestamp) to a JSON file for persistence. Checks session expiry.
*   **`validate_session()`:** Makes a lightweight API call to check if the current session cookies are still valid.
*   **`get_or_refresh_session()`:** Orchestrates session loading, validation, and refreshing (triggering login) if necessary.
*   **`fetch_jobs()`:** Makes the POST request to the `/bin/jobSearchServlet` API endpoint with the necessary payload, headers, and cookies to retrieve a page of job results.
*   **`fetch_with_retry()`:** Wraps `fetch_jobs` with retry logic using exponential backoff.
*   **`filter_jobs_by_state()`:** Filters the jobs returned by the API based on the `FILTER_STATE` configuration.
*   **`save_job_results()`:** Writes the final filtered list of jobs and metadata to the output JSON file.
*   **`scrape_roberthalf_jobs()`:** The main function that orchestrates the entire process: session management, API fetching loop, filtering, and saving results.

## Limitations and Considerations

*   **Website/API Changes:** The script relies on specific website login elements (locators) and the structure/behavior of the internal API. Changes by Robert Half can break the scraper.
*   **Login Fragility:** Automated login processes can be detected or changed, potentially requiring updates to the Playwright interaction logic. Captchas or MFA (if introduced) would require significant changes.
*   **Rate Limiting/Blocking:** Excessive requests might lead to IP blocking or throttling. The delays and proxy support are mitigation attempts.
*   **Session Validity:** Sessions can expire or be invalidated server-side for various reasons beyond the script's control. The validation and refresh logic attempt to handle this.
*   **Error Handling:** While retries and basic error handling are implemented, complex network issues or unexpected API responses might require more robust handling.

## API Documentation: `/bin/jobSearchServlet`

This section documents the internal Robert Half API endpoint used by the script to fetch job listings, based on observed network requests. ***Note: This is an internal API and is subject to change without notice.***

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
    | `keywords`     | String        | Job title, skill, or keyword search terms.                                                                 | `""` (Empty in example)                 |
    | `location`     | String        | General location search term (can be city, state, zip).                                                    | `""` (Empty in example)                 |
    | `distance`     | String        | Search radius around the location.                                                                         | `"50"`                                  |
    | `remote`       | String        | Filter for remote jobs ("Yes", "No", "").                                                                  | `"No"`                                  |
    | `source`       | List[String]  | Source system for jobs (e.g., Salesforce).                                                                 | `["Salesforce"]`                        |
    | `city`         | List[String]  | List of specific cities to filter by. *Note: The script doesn't use this, filtering by state post-API call.* | `["Kansas City", ...]` (in `curl` only) |
    | `lobid`        | List[String]  | Line of Business ID (e.g., "RHT" for Robert Half Technology). Likely important for scope.                | `["RHT"]`                               |
    | `postedwithin` | String        | Time frame for job posting date (maps to `JOB_POST_PERIOD`).                                               | `"PAST_24_HOURS"` (Configurable)        |
    | `pagesize`     | Integer       | Number of job results per page.                                                                            | `25`                                    |
    | `pagenumber`   | Integer       | The page number of results to retrieve (1-based).                                                          | `1`, `2`, ... (Iterated by script)      |
    | `sortby`       | String        | Sorting criteria (e.g., `PUBLISHED_DATE_DESC`, `RELEVANCE_DESC`).                                          | `"PUBLISHED_DATE_DESC"`                 |
    | `payratemin`   | Integer       | Minimum pay rate filter.                                                                                   | `0`                                     |
    | `stateprovince`| String        | *Not observed in request payload, filtering done client-side by script based on response.*                 | N/A (Used for filtering response)       |

*   **Example Request (`curl`):**
    ```bash
    curl 'https://www.roberthalf.com/bin/jobSearchServlet' \
      -H 'accept: application/json, text/plain, */*' \
      # ... other headers (cookie, user-agent, referer, etc.) ...
      -H 'content-type: application/json' \
      --data-raw '{"country":"us","keywords":"","location":"","distance":"50","remote":"No","remoteText":"","languagecodes":[],"source":["Salesforce"],"city":["Kansas City","King of Prussia","Orlando","Philadelphia"],"emptype":[],"lobid":["RHT"],"jobtype":"","postedwithin":"","timetype":"","pagesize":25,"pagenumber":1,"sortby":"RELEVANCE_DESC","mode":"","payratemin":0,"includedoe":""}'
    ```

*   **Example Successful Response (JSON):**
    The response is a JSON object containing metadata and the job listings.
    ```json
    {
        "aws_region": "us-west-2",
        "request_id": "da345274-2fda-4bb5-a69c-1a7040a6054f",
        "request_status": "SUCCESS",
        "request_message": "OK",
        "found": "54", // Total number of jobs matching the criteria
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
                "remote": "No",
                "job_detail_url": "https://www.roberthalf.com/us/en/job/...",
                "source": "Salesforce",
                // ... other fields
            },
            // ... more job objects
        ]
    }
    ```
    **Key Response Fields:**
    *   `found`: String representing the total number of jobs found matching the criteria across all pages.
    *   `facets`: An object containing counts for various filters (employment type, city, posted date), useful for UI refinements but not directly used by this script.
    *   `jobs`: An array containing the job listing objects for the current page.
    *   `jobs[].jobtitle`: The title of the job.
    *   `jobs[].description`: Job description, often contains HTML.
    *   `jobs[].city`, `jobs[].stateprovince`, `jobs[].postalcode`: Location details. `stateprovince` is used by the script for filtering.
    *   `jobs[].date_posted`: The date the job was posted (ISO 8601 format).
    *   `jobs[].payrate_min`, `jobs[].payrate_max`, `jobs[].payrate_period`: Salary/pay information, if available.
    *   `jobs[].job_detail_url`: Direct link to the job posting page.
    *   `jobs[].emptype`: Type of employment (Perm, Temp, etc.).

*   **Error Handling:** The script checks for non-2xx HTTP status codes. Status codes like 401/403 likely indicate an invalid or expired session (triggering re-login). Invalid JSON responses also indicate potential session issues or API errors.
