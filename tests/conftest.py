"""Test-only defaults so importing application config does not require secrets.

Production startup still validates required credentials because this file is only
loaded by pytest. Never use these test settings to run the bot in production.
"""
import os

os.environ.setdefault("REQUIRE_VALID_CONFIG", "false")
