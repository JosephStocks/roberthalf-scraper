#!/bin/bash

# Stop on errors
set -e

# Define source and target directories
SOURCE_DIR="systemd"
TARGET_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="roberthalf-scraper.service"
TIMER_FILE="roberthalf-scraper.timer"

# Check if source files exist
if [ ! -f "$SOURCE_DIR/$SERVICE_FILE" ]; then
    echo "Error: Service file '$SOURCE_DIR/$SERVICE_FILE' not found."
    exit 1
fi
if [ ! -f "$SOURCE_DIR/$TIMER_FILE" ]; then
    echo "Error: Timer file '$SOURCE_DIR/$TIMER_FILE' not found."
    exit 1
fi

# Create the target directory if it doesn't exist
echo "Creating target directory (if needed): $TARGET_DIR"
mkdir -p "$TARGET_DIR"

# Copy the unit files
echo "Copying unit files..."
cp "$SOURCE_DIR/$SERVICE_FILE" "$TARGET_DIR/"
cp "$SOURCE_DIR/$TIMER_FILE" "$TARGET_DIR/"

# Reload systemd user daemon
echo "Reloading systemd user daemon..."
systemctl --user daemon-reload

# Enable and start the timer
echo "Enabling and starting the timer '$TIMER_FILE'..."
systemctl --user enable --now "$TIMER_FILE"

echo ""
echo "Installation complete."
echo "---------------------"
echo "To check timer status: systemctl --user status $TIMER_FILE"
echo "To check service status: systemctl --user status $SERVICE_FILE"
echo "To view service logs: journalctl --user -u $SERVICE_FILE -f"
echo "To list all user timers: systemctl --user list-timers" 