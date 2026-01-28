"""FastAPI application entry point.

This module creates and configures the FastAPI application instance
for the hotel search and scoring service.
"""

from api import create_app

app = create_app()
