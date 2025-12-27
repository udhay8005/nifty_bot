import os
import logging
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("Security")

# Load Key from Environment
_key = os.getenv('FERNET_KEY')
_cipher_suite = None

# Initialize Cipher Suite
if _key:
    try:
        _cipher_suite = Fernet(_key)
        # logger.info("🔐 Security Module Loaded. Data will be encrypted.")
    except Exception as e:
        logger.critical(f"❌ Invalid FERNET_KEY in .env: {e}")
        logger.warning("⚠️ Security Module Disabled. Data will be stored in PLAIN TEXT.")
else:
    logger.warning("⚠️ No FERNET_KEY found. Security Module Disabled (Plain Text Mode).")

def encrypt_value(value: str) -> str:
    """
    Encrypts a string value.
    Returns plain text if security is disabled.
    """
    if not value:
        return ""
    
    if not _cipher_suite:
        return value

    try:
        # Fernet expects bytes, returns bytes. We decode back to utf-8 string for DB storage.
        encrypted_bytes = _cipher_suite.encrypt(value.encode('utf-8'))
        return encrypted_bytes.decode('utf-8')
    except Exception as e:
        logger.error(f"Encryption failed: {e}")
        return value

def decrypt_value(value: str) -> str:
    """
    Decrypts a string value.
    
    🧠 INTELLIGENT FAIL-SAFE:
    If decryption fails (InvalidToken), it assumes the data is 
    plain text (legacy data or key mismatch) and returns it as-is.
    This prevents the bot from crashing due to DB inconsistencies.
    """
    if not value:
        return ""

    if not _cipher_suite:
        return value

    try:
        # Expects base64 encoded string (which is what Fernet produces)
        decrypted_bytes = _cipher_suite.decrypt(value.encode('utf-8'))
        return decrypted_bytes.decode('utf-8')
    
    except InvalidToken:
        # This is common if you switched keys or migrated from plain text DB
        logger.warning("⚠️ Decryption failed (Token Invalid). Assuming Plain Text.")
        return value
        
    except Exception as e:
        logger.error(f"Decryption error: {e}")
        return value