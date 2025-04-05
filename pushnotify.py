#!/usr/bin/env python3

import os
import sys
from typing import Any, Literal

import requests

# Retrieve the API token and user keys from environment variables
PUSHOVER_API_TOKEN: str = os.getenv("PUSHOVER_API_TOKEN")
USER_KEYS: dict[str, str] = {
    "Joe": os.getenv("PUSHOVER_USER_KEY_JOE"),
    "Katie": os.getenv("PUSHOVER_USER_KEY_KATIE"),
}


def send_pushover_notification(
    message: str, user: Literal["Joe", "Katie", "All"] = "Joe", **kwargs: dict[str, Any]
) -> None:
    """
    Sends a notification via Pushover.

    :param message: The message to be sent (required)
    :param user: The recipient ('Joe' by default, or 'Katie' or 'All' for both)
    :param kwargs: Optional parameters like title, url, sound, etc.
    """
    if user not in USER_KEYS and user != "All":
        raise ValueError("User must be 'Joe', 'Katie', or 'All'")

    # Check if API token is available
    if not PUSHOVER_API_TOKEN:
        print("Error: PUSHOVER_API_TOKEN is not set.")
        sys.exit(1)

    # Select the user keys
    if user == "All":
        user_key = ",".join(filter(None, USER_KEYS.values()))
        if not user_key:
            print("Error: User keys for 'Joe' and 'Katie' are not set.")
            sys.exit(1)
    else:
        user_key = USER_KEYS.get(user)
        if not user_key:
            print(f"Error: User key for '{user}' is not set.")
            sys.exit(1)

    # The data payload
    data: dict[str, Any] = {
        "token": PUSHOVER_API_TOKEN,
        "user": user_key,
        "message": message,
    }

    # Add optional parameters if provided
    data.update(kwargs)

    # Send the POST request
    response: requests.Response = requests.post(
        "https://api.pushover.net/1/messages.json", data=data
    )

    # Handle the response
    if response.status_code == 200:
        print("Notification sent successfully.")
    else:
        print(f"Error sending notification: {response.status_code} - {response.text}")


if __name__ == "__main__":
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
    parser.add_argument("--sound", help="Optional sound to use.")

    args = parser.parse_args()

    kwargs = {
        k: v
        for k, v in vars(args).items()
        if k not in ("message", "user") and v is not None
    }

    send_pushover_notification(args.message, user=args.user, **kwargs)
