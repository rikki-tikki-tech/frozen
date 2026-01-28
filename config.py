"""Application configuration loaded from environment variables."""

import os

from dotenv import load_dotenv

load_dotenv()

# ETG API
ETG_KEY_ID: str = os.environ["ETG_KEY_ID"]
ETG_API_KEY: str = os.environ["ETG_API_KEY"]
ETG_REQUEST_TIMEOUT: float = float(os.environ.get("ETG_REQUEST_TIMEOUT", "30.0"))

# LLM Scoring
GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")
SCORING_MODEL: str = os.environ.get("SCORING_MODEL", "gemini-3-flash-preview")

# CORS
CORS_ORIGINS: list[str] = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ORIGINS", "http://localhost:5173,http://34.118.32.192"
    ).split(",")
]
