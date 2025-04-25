#!/usr/bin/env python3
"""
Job scraper main script - Supports multiple job sites including Robert Half and Keurig Dr Pepper.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

from job_scraper_design import Config, JobScraperManager


def setup_argument_parser() -> argparse.ArgumentParser:
    """Set up command line argument parser."""
    parser = argparse.ArgumentParser(
        description="Scrape jobs from multiple sources and generate reports."
    )

    # Basic options
    parser.add_argument(
        "--config", "-c", default=".env", help="Path to configuration file (default: .env)"
    )
    parser.add_argument(
        "--log-level",
        "-l",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Log level (overrides config file)",
    )
    parser.add_argument(
        "--output-dir", "-o", help="Output directory for reports (overrides config)"
    )

    # Source selection
    parser.add_argument(
        "--sources",
        "-s",
        help="Comma-separated list of sources to scrape (default: all enabled sources)",
    )
    parser.add_argument(
        "--disable-robert-half", action="store_true", help="Disable Robert Half scraper"
    )
    parser.add_argument("--enable-kdp", action="store_true", help="Enable Keurig Dr Pepper scraper")

    # Analysis options
    parser.add_argument(
        "--analyze-all",
        "-a",
        action="store_true",
        help="Analyze all jobs, not just new ones (costs more API credits)",
    )
    parser.add_argument(
        "--llm-debug", action="store_true", help="Enable verbose LLM debugging logs"
    )

    # Display options
    parser.add_argument(
        "--test-mode",
        "-t",
        action="store_true",
        help="Run in test mode (enables notifications even if no new jobs)",
    )
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress console output")
    parser.add_argument("--version", "-v", action="store_true", help="Display version and exit")

    return parser


def configure_logging(config: Config, args: argparse.Namespace) -> None:
    """Configure logging based on config and command line arguments."""
    # Determine log level from args or config
    log_level_str = args.log_level or config.get("LOG_LEVEL", "INFO")
    log_level = getattr(logging, log_level_str.upper(), logging.INFO)

    # Get log directory from config
    log_dir = Path(config.get("LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)

    # Create log file name with timestamp
    log_file = log_dir / f"job_scraper_{time.strftime('%Y%m%d_%H%M%S')}.log"

    # Configure root logger
    handlers = []

    # File handler always created
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(levelname)s [%(name)s:%(lineno)d] - %(message)s")
    )
    handlers.append(file_handler)

    # Console handler only if not quiet mode
    if not args.quiet:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(logging.Formatter("%(levelname)s [%(name)s] - %(message)s"))
        handlers.append(console_handler)

    # Configure root logger
    logging.basicConfig(
        level=log_level,
        handlers=handlers,
        force=True,  # Python 3.8+: force reconfiguration
    )

    # Set lower levels for noisy libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


def apply_cli_overrides(config: Config, args: argparse.Namespace) -> None:
    """Apply command line argument overrides to config."""
    # Override output directory
    if args.output_dir:
        config._config["OUTPUT_DIR"] = args.output_dir
        config._config["DOCS_DIR"] = args.output_dir

    # Override source settings
    if args.disable_robert_half:
        config._config["ROBERTHALF_ENABLED"] = False

    if args.enable_kdp:
        config._config["KDP_ENABLED"] = True

    # Set test mode
    if args.test_mode:
        config._config["TEST_MODE"] = True


def main() -> int:
    """Main entry point."""
    # Parse command line arguments
    parser = setup_argument_parser()
    args = parser.parse_args()

    # Check for version flag
    if args.version:
        print("Job Scraper v1.0.0")
        return 0

    # Load configuration
    try:
        config = Config(args.config)
    except Exception as e:
        print(f"Error loading configuration: {e}")
        return 1

    # Apply command line overrides
    apply_cli_overrides(config, args)

    # Configure logging
    configure_logging(config, args)
    logger = logging.getLogger(__name__)

    # Log startup information
    logger.info("Starting Job Scraper v1.0.0")

    try:
        # Create and run scraper manager
        manager = JobScraperManager(config, args.llm_debug)
        manager.run(analyze_all=args.analyze_all)
        logger.info("Job scraping completed successfully")
        return 0
    except KeyboardInterrupt:
        logger.info("Job scraping interrupted by user")
        return 130  # Standard exit code for SIGINT
    except Exception as e:
        logger.exception(f"Error during job scraping: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
