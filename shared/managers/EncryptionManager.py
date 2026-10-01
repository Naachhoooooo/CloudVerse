import os
import base64
import json
from typing import Union, Optional
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend
from shared.core.Logger import get_logger

logger = get_logger(__name__)


class EncryptionManager:
    """
    Centralized encryption manager for CloudVerse.
    Provides secure encryption/decryption for credentials, sessions, and sensitive data.

    Args:
        master_password: Master password / encryption key string (required).
        salt_b64: Base64-encoded salt string. If None a fresh salt is generated
                  (log the generated value and persist it in .env).
    """

    def __init__(self, master_password: str, salt_b64: Optional[str] = None):
        if not master_password:
            raise ValueError("master_password is required for EncryptionManager")
        self.master_password = master_password
        self.salt = self._resolve_salt(salt_b64)

        # Initialize encryption contexts
        self._setup_encryption_contexts()

        # Scrub raw password from heap — keys are already derived
        self.master_password = None

        logger.info("EncryptionManager initialized successfully")

    def _resolve_salt(self, salt_b64: Optional[str]) -> bytes:
        """Decode provided salt or generate a new one."""
        if salt_b64:
            try:
                return base64.b64decode(salt_b64.encode())
            except Exception:
                logger.warning("Invalid salt_b64 provided, generating new one")

        # Generate new salt
        salt = os.urandom(32)
        generated_b64 = base64.b64encode(salt).decode()
        logger.info(
            f"Generated new encryption salt. Store in .env: ENCRYPTION_SALT={generated_b64}"
        )
        return salt
    
    def _derive_key(self, context: str, iterations: int = 100000) -> bytes:
        """
        Derive encryption key using PBKDF2.
        
        Args:
            context: Context string to create different keys for different purposes
            iterations: Number of PBKDF2 iterations
            
        Returns:
            Derived encryption key
        """
        # Create context-specific salt
        context_salt = self.salt + context.encode()
        
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=context_salt,
            iterations=iterations,
            backend=default_backend()
        )
        
        key = kdf.derive(self.master_password.encode())
        return key
    
    def _setup_encryption_contexts(self):
        """Setup different encryption contexts for different data types."""
        try:
            # Different contexts for different data types
            self.credential_cipher = AESGCM(self._derive_key("credentials"))
            self.session_cipher = AESGCM(self._derive_key("sessions"))
            self.general_cipher = AESGCM(self._derive_key("general"))
            self.database_cipher = AESGCM(self._derive_key("database"))
            
            logger.debug("Encryption contexts initialized")
        except Exception as e:
            logger.error(f"Failed to setup encryption contexts: {e}")
            raise
    
    def encrypt_credentials(self, data: Union[str, dict], telegram_id: str) -> str:
        """
        Encrypt Google Drive credentials or other sensitive authentication data.
        
        Args:
            data: Credential data to encrypt
            telegram_id: User's telegram_id for Associated Data (AAD) binding
            
        Returns:
            Base64 encoded encrypted data
        """
        try:
            if isinstance(data, dict):
                data = json.dumps(data)
            
            nonce = os.urandom(12)
            aad = str(telegram_id).encode() if telegram_id else None
            ciphertext = self.credential_cipher.encrypt(nonce, data.encode(), aad)
            return base64.b64encode(nonce + ciphertext).decode()
        except Exception as e:
            logger.error(f"Failed to encrypt credentials: {e}")
            raise
    
    def decrypt_credentials(self, encrypted_data: str, telegram_id: str) -> Union[str, dict]:
        """
        Decrypt Google Drive credentials.
        
        Args:
            encrypted_data: Base64 encoded encrypted data
            telegram_id: User's telegram_id for Associated Data (AAD) verification
            
        Returns:
            Decrypted credential data
        """
        try:
            encrypted_bytes = base64.b64decode(encrypted_data.encode())
            nonce = encrypted_bytes[:12]
            ciphertext = encrypted_bytes[12:]
            aad = str(telegram_id).encode() if telegram_id else None
            decrypted = self.credential_cipher.decrypt(nonce, ciphertext, aad)
            decrypted_str = decrypted.decode()
            
            # Try to parse as JSON, return string if not valid JSON
            try:
                return json.loads(decrypted_str)
            except json.JSONDecodeError:
                return decrypted_str
        except Exception as e:
            logger.error(f"Failed to decrypt credentials: {e}")
            raise
    
    def encrypt_session_data(self, data: Union[str, dict]) -> str:
        """
        Encrypt session data like Telegram session strings.
        
        Args:
            data: Session data to encrypt
            
        Returns:
            Base64 encoded encrypted data
        """
        try:
            if isinstance(data, dict):
                data = json.dumps(data)
            
            nonce = os.urandom(12)
            ciphertext = self.session_cipher.encrypt(nonce, data.encode(), None)
            return base64.b64encode(nonce + ciphertext).decode()
        except Exception as e:
            logger.error(f"Failed to encrypt session data: {e}")
            raise
    
    def decrypt_session_data(self, encrypted_data: str) -> Union[str, dict]:
        """
        Decrypt session data.
        
        Args:
            encrypted_data: Base64 encoded encrypted data
            
        Returns:
            Decrypted session data
        """
        try:
            encrypted_bytes = base64.b64decode(encrypted_data.encode())
            nonce = encrypted_bytes[:12]
            ciphertext = encrypted_bytes[12:]
            decrypted = self.session_cipher.decrypt(nonce, ciphertext, None)
            decrypted_str = decrypted.decode()
            
            # Try to parse as JSON, return string if not valid JSON
            try:
                return json.loads(decrypted_str)
            except json.JSONDecodeError:
                return decrypted_str
        except Exception as e:
            logger.error(f"Failed to decrypt session data: {e}")
            raise
    
    def encrypt_general(self, data: Union[str, dict]) -> str:
        """
        Encrypt general sensitive data.
        
        Args:
            data: Data to encrypt
            
        Returns:
            Base64 encoded encrypted data
        """
        try:
            if isinstance(data, dict):
                data = json.dumps(data)
            
            nonce = os.urandom(12)
            ciphertext = self.general_cipher.encrypt(nonce, data.encode(), None)
            return base64.b64encode(nonce + ciphertext).decode()
        except Exception as e:
            logger.error(f"Failed to encrypt general data: {e}")
            raise
    
    def decrypt_general(self, encrypted_data: str) -> Union[str, dict]:
        """
        Decrypt general sensitive data.
        
        Args:
            encrypted_data: Base64 encoded encrypted data
            
        Returns:
            Decrypted data
        """
        try:
            encrypted_bytes = base64.b64decode(encrypted_data.encode())
            nonce = encrypted_bytes[:12]
            ciphertext = encrypted_bytes[12:]
            decrypted = self.general_cipher.decrypt(nonce, ciphertext, None)
            decrypted_str = decrypted.decode()
            
            # Try to parse as JSON, return string if not valid JSON
            try:
                return json.loads(decrypted_str)
            except json.JSONDecodeError:
                return decrypted_str
        except Exception as e:
            logger.error(f"Failed to decrypt general data: {e}")
            raise
    
    def encrypt_database_field(self, data: str) -> str:
        """
        Encrypt sensitive database fields.
        
        Args:
            data: Database field data to encrypt
            
        Returns:
            Base64 encoded encrypted data
        """
        try:
            nonce = os.urandom(12)
            ciphertext = self.database_cipher.encrypt(nonce, data.encode(), None)
            return base64.b64encode(nonce + ciphertext).decode()
        except Exception as e:
            logger.error(f"Failed to encrypt database field: {e}")
            raise
    
    def decrypt_database_field(self, encrypted_data: str) -> str:
        """
        Decrypt sensitive database fields.
        
        Args:
            encrypted_data: Base64 encoded encrypted data
            
        Returns:
            Decrypted database field data
        """
        try:
            encrypted_bytes = base64.b64decode(encrypted_data.encode())
            nonce = encrypted_bytes[:12]
            ciphertext = encrypted_bytes[12:]
            decrypted = self.database_cipher.decrypt(nonce, ciphertext, None)
            return decrypted.decode()
        except Exception as e:
            logger.error(f"Failed to decrypt database field: {e}")
            raise
    
    def generate_secure_token(self, length: int = 32) -> str:
        """
        Generate a secure random token.
        
        Args:
            length: Length of the token in bytes
            
        Returns:
            Base64 encoded secure token
        """
        token = os.urandom(length)
        return base64.urlsafe_b64encode(token).decode()
    
    def verify_encryption_health(self) -> bool:
        """
        Verify that encryption/decryption is working correctly.
        
        Returns:
            True if encryption is healthy, False otherwise
        """
        try:
            test_data = "encryption_health_check"
            
            # Test all encryption contexts
            contexts = [
                (lambda x: self.encrypt_credentials(x, "test_user"), lambda x: self.decrypt_credentials(x, "test_user")),
                (self.encrypt_session_data, self.decrypt_session_data),
                (self.encrypt_general, self.decrypt_general),
                (self.encrypt_database_field, self.decrypt_database_field)
            ]
            
            for encrypt_func, decrypt_func in contexts:
                encrypted = encrypt_func(test_data)
                decrypted = decrypt_func(encrypted)
                
                if decrypted != test_data:
                    logger.error(f"Encryption health check failed for {encrypt_func.__name__}")
                    return False
            
            logger.debug("Encryption health check passed")
            return True
        except Exception as e:
            logger.error(f"Encryption health check failed: {e}")
            return False
    
    def rotate_keys(self, new_master_password: str) -> bool:
        """
        Rotate encryption keys with a new master password.
        WARNING: This will invalidate all existing encrypted data.
        
        Args:
            new_master_password: New master password for key derivation
            
        Returns:
            True if rotation successful, False otherwise
        """
        try:
            logger.warning("Starting key rotation - this will invalidate existing encrypted data")
            
            # Snapshot current ciphers so we can rollback without needing the old password
            old_ciphers = (
                self.credential_cipher, self.session_cipher,
                self.general_cipher, self.database_cipher,
            )
            self.master_password = new_master_password
            
            # Re-setup encryption contexts with new keys
            self._setup_encryption_contexts()
            
            # Re-scrub password from heap after derivation
            self.master_password = None
            
            # Verify new encryption works
            if self.verify_encryption_health():
                logger.info("Key rotation completed successfully")
                return True
            else:
                # Rollback — restore previous cipher objects directly
                (self.credential_cipher, self.session_cipher,
                 self.general_cipher, self.database_cipher) = old_ciphers
                logger.error("Key rotation failed, rolled back to old keys")
                return False
                
        except Exception as e:
            self.master_password = None
            logger.error(f"Key rotation failed: {e}")
            return False


# Global encryption manager instance
_encryption_manager: Optional[EncryptionManager] = None


def get_encryption_manager() -> EncryptionManager:
    """
    Return the already-initialized global EncryptionManager.

    Must call initialize_encryption_manager(master_password, salt_b64) first.
    Raises RuntimeError if not yet initialized.
    """
    if _encryption_manager is None:
        raise RuntimeError(
            "EncryptionManager has not been initialized. "
            "Call initialize_encryption_manager(master_password, salt_b64) at startup."
        )
    return _encryption_manager


def initialize_encryption_manager(master_password: str, salt_b64: Optional[str] = None) -> EncryptionManager:
    """
    Initialize the global EncryptionManager.

    Args:
        master_password: Encryption key / master password string (required).
        salt_b64: Base64-encoded salt. If None, a fresh salt is generated.

    Returns:
        EncryptionManager instance
    """
    global _encryption_manager
    _encryption_manager = EncryptionManager(master_password, salt_b64)
    return _encryption_manager


# Convenience functions — require initialization first
def encrypt_credential(data: Union[str, dict], telegram_id: str) -> str:
    """Encrypt credential data using the global encryption manager."""
    return get_encryption_manager().encrypt_credentials(data, telegram_id)


def decrypt_credential(encrypted_data: str, telegram_id: str) -> Union[str, dict]:
    """Decrypt credential data using the global encryption manager."""
    return get_encryption_manager().decrypt_credentials(encrypted_data, telegram_id)


def encrypt_session(data: Union[str, dict]) -> str:
    """Encrypt session data using the global encryption manager."""
    return get_encryption_manager().encrypt_session_data(data)


def decrypt_session(encrypted_data: str) -> Union[str, dict]:
    """Decrypt session data using the global encryption manager."""
    return get_encryption_manager().decrypt_session_data(encrypted_data)


def generate_token(length: int = 32) -> str:
    """Generate a secure random token."""
    return get_encryption_manager().generate_secure_token(length)
