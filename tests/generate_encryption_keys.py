#!/usr/bin/env python3
"""
Generate secure encryption keys and salts for CloudVerse Google Drive Bot.
Run this script to generate new encryption credentials.
"""

import os
import base64
from cryptography.fernet import Fernet

def generate_encryption_key():
    """Generate a secure encryption key for Fernet."""
    return Fernet.generate_key().decode()

def generate_salt(length=32):
    """Generate a secure random salt."""
    salt = os.urandom(length)
    return base64.b64encode(salt).decode()

def generate_secure_password(length=32):
    """Generate a secure random password."""
    return base64.urlsafe_b64encode(os.urandom(length)).decode()

def main():
    print("🔐 CloudVerse Encryption Key Generator")
    print("=" * 50)
    
    # Generate encryption key
    encryption_key = generate_encryption_key()
    print(f"ENCRYPTION_KEY={encryption_key}")
    
    # Generate salt
    encryption_salt = generate_salt()
    print(f"ENCRYPTION_SALT={encryption_salt}")
    
    # Generate additional secure tokens if needed
    secure_token = generate_secure_password(24)
    print(f"SECURE_TOKEN={secure_token}")
    
    print("\n" + "=" * 50)
    print("📋 Instructions:")
    print("1. Copy the above values to your .env file")
    print("2. Keep these values secure and never commit them to version control")
    print("3. Use different keys for different environments (dev/prod)")
    print("4. Store production keys in secure environment variable systems")
    
    print("\n🔒 Security Notes:")
    print("- ENCRYPTION_KEY: Used by EncryptionManager for PBKDF2 key derivation")
    print("- ENCRYPTION_SALT: Used to ensure unique keys even with same password")
    print("- SECURE_TOKEN: Additional token for other security purposes")
    
    print("\n📝 Example .env file:")
    print("# CloudVerse Encryption Configuration")
    print(f"ENCRYPTION_KEY={encryption_key}")
    print(f"ENCRYPTION_SALT={encryption_salt}")
    print("# Other configuration...")
    print("BOT_TOKEN=your_telegram_bot_token")
    print("SUPER_ADMIN_ID=your_telegram_id")

if __name__ == "__main__":
    main()
