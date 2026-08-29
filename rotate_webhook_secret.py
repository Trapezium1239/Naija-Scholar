#!/usr/bin/env python3
"""
Webhook Secret Rotation Script for Naija Scholar V2.

This script helps rotate webhook secrets (Telegram, Paystack) safely.
It generates new secrets and provides instructions for updating them
in the deployment environment (Render, .env, etc.).

Usage:
    python rotate_webhook_secret.py --service telegram
    python rotate_webhook_secret.py --service paystack
    python rotate_webhook_secret.py --service all
"""

import argparse
import os
import secrets
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import main


def generate_secret(length: int = 32) -> str:
    """Generate a cryptographically secure random secret."""
    return secrets.token_urlsafe(length)


def rotate_telegram_secret():
    """Generate new Telegram webhook secret token."""
    new_secret = generate_secret(32)
    print("=" * 60)
    print("TELEGRAM WEBHOOK SECRET ROTATION")
    print("=" * 60)
    print(f"New TELEGRAM_WEBHOOK_SECRET: {new_secret}")
    print()
    print("Next steps:")
    print("1. Update your deployment environment (Render, .env, etc.)")
    print("2. Set TELEGRAM_WEBHOOK_SECRET to the new value above")
    print("3. Restart the application")
    print("4. Update Telegram webhook URL if using webhook mode:")
    print(f"   https://api.telegram.org/bot<BOT_TOKEN>/setWebhook?url=<YOUR_WEBHOOK_URL>&secret_token={new_secret}")
    print()
    print("Note: The secret_token parameter in setWebhook is optional but recommended")
    print("      for verifying incoming webhook requests from Telegram.")
    return new_secret


def rotate_paystack_secret():
    """Generate new Paystack webhook secret."""
    new_secret = generate_secret(32)
    print("=" * 60)
    print("PAYSTACK WEBHOOK SECRET ROTATION")
    print("=" * 60)
    print(f"New PAYSTACK_WEBHOOK_SECRET: {new_secret}")
    print()
    print("Next steps:")
    print("1. Update your deployment environment (Render, .env, etc.)")
    print("2. Set PAYSTACK_WEBHOOK_SECRET to the new value above")
    print("3. Update Paystack dashboard:")
    print("   - Go to Settings > Webhooks")
    print("   - Update the webhook secret for each webhook endpoint")
    print("4. Restart the application")
    print()
    print("Note: In production, PAYSTACK_WEBHOOK_SECRET is REQUIRED.")
    print("      The app will reject webhooks without it when ENVIRONMENT=production.")
    return new_secret


def rotate_all():
    """Rotate all webhook secrets."""
    print("=" * 60)
    print("ROTATING ALL WEBHOOK SECRETS")
    print("=" * 60)
    print()
    rotate_telegram_secret()
    print()
    rotate_paystack_secret()
    print()
    print("=" * 60)
    print("All secrets rotated. Update your deployment environment.")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Rotate webhook secrets for Naija Scholar V2")
    parser.add_argument(
        "--service",
        choices=["telegram", "paystack", "all"],
        default="all",
        help="Which service secret to rotate (default: all)",
    )
    args = parser.parse_args()

    if args.service == "telegram":
        rotate_telegram_secret()
    elif args.service == "paystack":
        rotate_paystack_secret()
    else:
        rotate_all()


if __name__ == "__main__":
    main()