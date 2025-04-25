#!/usr/bin/env python3
import logging
import os
from typing import Any, Literal

import requests

# Get a logger instance for this module
# It will inherit level/handlers from the root logger configured in roberthalf_scraper.py
logger = logging.getLogger(__name__)

PUSHOVER_API_TOKEN: str | None = os.getenv("PUSHOVER_API_TOKEN")
USER_KEYS: dict[str, str | None] = {
    "Joe": os.getenv("PUSHOVER_USER_KEY_JOE"),
    "Katie": os.getenv("PUSHOVER_USER_KEY_KATIE"),
}


def send_pushover_notification(
    message: str, user: Literal["Joe", "Katie", "All"] = "Joe", **kwargs: Any
) -> None:
    """
    Sends a notification via Pushover.

    :param message: The message to be sent (required)
    :param user: The recipient ('Joe' by default, or 'Katie' or 'All' for both)
    :param kwargs: Optional parameters like title, url, sound, html=1, etc.
                   See https://pushover.net/api#messages for all options.
    """
    # Clean up kwargs: remove None values as Pushover API might not like them
    kwargs = {k: v for k, v in kwargs.items() if v is not None}

    if user not in USER_KEYS and user != "All":
        logger.error(f"Invalid Pushover user specified: '{user}'. Must be 'Joe', 'Katie', or 'All'.")
        # Decide whether to raise or just exit/return
        # raise ValueError("User must be 'Joe', 'Katie', or 'All'")
        return # Don't send if user is invalid

    # Check if API token is available
    if not PUSHOVER_API_TOKEN:
        logger.error("PUSHOVER_API_TOKEN is not set. Cannot send notification.")
        # Avoid sys.exit(1) if called as a library
        return

    target_user_keys = []
    if user == "All":
        if USER_KEYS.get("Joe"):
            target_user_keys.append(USER_KEYS["Joe"])
        if USER_KEYS.get("Katie"):
            target_user_keys.append(USER_KEYS["Katie"])
        if not target_user_keys:
             logger.error("No user keys found for 'Joe' or 'Katie'. Cannot send notification.")
             return
    else:
        user_key = USER_KEYS.get(user)
        if not user_key:
            logger.error(f"User key for '{user}' (PUSHOVER_USER_KEY_{user.upper()}) is not set. Cannot send notification.")
            return
        target_user_keys.append(user_key)

    # Ensure message is not overly long (Pushover limit is 1024 bytes)
    # Simple check, actual byte length can vary with encoding
    if len(message.encode('utf-8')) > 1024: # Check byte length
         logger.warning("Message length is potentially too long for Pushover (>1024 bytes), truncating.")
         # Truncate based on bytes (more accurate)
         encoded_message = message.encode('utf-8')[:1020] # Truncate slightly below limit
         try:
             message = encoded_message.decode('utf-8', errors='ignore') + "..."
         except UnicodeDecodeError:
             # Fallback if truncation broke a character
             message = encoded_message.decode('utf-8', errors='replace') + "..."
         logger.warning(f"Truncated message length: {len(message.encode('utf-8'))} bytes")


    # The base data payload
    data: dict[str, Any] = {
        "token": PUSHOVER_API_TOKEN,
        "user": ",".join(target_user_keys),
        "message": message,
    }

    # Add optional parameters from kwargs
    data.update(kwargs)

    # Ensure boolean parameters are sent as 1 or 0 if present
    if 'html' in data:
        data['html'] = 1 if data['html'] else 0
    if 'monospace' in data:
         data['monospace'] = 1 if data['monospace'] else 0
         # Ensure html and monospace are not both set to 1
         if data.get('html') == 1 and data.get('monospace') == 1:
              logger.warning("Both 'html' and 'monospace' flags set for Pushover, disabling 'monospace'.")
              data['monospace'] = 0

    # Send the POST request
    try:
        logger.info(f"Sending Pushover notification to user(s): {user}")
        logger.debug(f"Pushover payload (excluding token/user keys): "
                       f"{ {k: v for k, v in data.items() if k not in ['token', 'user']} }")

        response = requests.post(
            "https://api.pushover.net/1/messages.json",
            data=data,
            timeout=15 # Add a reasonable timeout
        )
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

        logger.info("Pushover notification sent successfully.")
        # Optional: Log response details on success if needed for debugging receipts etc.
        # try:
        #     response_json = response.json()
        #     logger.debug(f"Pushover API Response: {response_json}")
        # except json.JSONDecodeError:
        #     logger.debug("Pushover API Response was not valid JSON (but status code was OK).")

    except requests.exceptions.Timeout:
        logger.error("Error sending Pushover notification: Request timed out.")
    except requests.exceptions.RequestException as e:
        # Log detailed error including response if available
        error_details = str(e)
        if e.response is not None:
             try:
                  # Try to get JSON error details from Pushover response
                  error_json = e.response.json()
                  errors = error_json.get('errors', [])
                  user_error = error_json.get('user', 'N/A')
                  status = error_json.get('status', e.response.status_code)
                  error_details = f"Status {status}, User Invalid: '{user_error}', Errors: {', '.join(errors)}"
             except ValueError: # Includes JSONDecodeError
                  # Response was not JSON, log raw text snippet
                  error_details = f"Status {e.response.status_code}, Response: {e.response.text[:200]}..." # Log first 200 chars
        logger.error(f"Error sending Pushover notification: {error_details}")
    except Exception as e:
        # Catch any other unexpected errors
        logger.error(f"Unexpected error sending Pushover notification: {e}", exc_info=True)



if __name__ == "__main__":
    # Add basic logging config if running standalone
    if not logging.getLogger().hasHandlers():
        logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    import argparse

    parser = argparse.ArgumentParser(description="Send a Pushover notification.")
    parser.add_argument("message", help="The message to send.")
    parser.add_argument(
        "--user",
        choices=["Joe", "Katie", "All"],
        default="Joe",
        help="The recipient of the message (default: Joe).",
    )
    parser.add_argument("--title", help="Optional title of the message.")
    parser.add_argument("--url", help="Optional URL to include.")
    parser.add_argument("--url_title", help="Optional title for the URL.")
    parser.add_argument("--sound", help="Optional sound to use.")
    parser.add_argument("--html", action="store_true", help="Enable HTML formatting in the message.")

    args = parser.parse_args()

    # Prepare kwargs, converting 'html' action to 1/0 if present
    kwargs = {
        k: v
        for k, v in vars(args).items()
        if k not in ("message", "user", "html") and v is not None # Exclude html initially
    }
    if args.html:
        kwargs['html'] = 1 # Add html=1 if flag was passed

    send_pushover_notification(args.message, user=args.user, **kwargs)
