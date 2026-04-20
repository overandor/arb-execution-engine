"""
Signer - Burner wallet management and transaction signing
"""

import json
import os
from typing import Optional
from pathlib import Path
from solana.keypair import Keypair
from solana.publickey import PublicKey
from base64 import b64decode, b64encode
from solders.transaction import VersionedTransaction

class Signer:
    def __init__(self, private_key: Optional[str] = None, keypair_path: Optional[str] = None):
        """
        Initialize signer with private key or keypair file
        
        Args:
            private_key: Base64-encoded private key
            keypair_path: Path to keypair JSON file
        """
        if private_key:
            self.keypair = Keypair.from_bytes(b64decode(private_key))
        elif keypair_path and os.path.exists(keypair_path):
            with open(keypair_path, 'r') as f:
                keypair_data = json.load(f)
                self.keypair = Keypair.from_secret_key(bytes(keypair_data))
        else:
            # Generate new keypair
            self.keypair = Keypair()
            print(f"Generated new keypair. Public key: {self.keypair.public_key}")
            print(f"Private key (base64): {b64encode(self.keypair.secret()).decode()}")
    
    @property
    def public_key(self) -> str:
        return str(self.keypair.public_key)
    
    @property
    def private_key_base64(self) -> str:
        return b64encode(self.keypair.secret()).decode()
    
    def save_keypair(self, path: str):
        """Save keypair to file"""
        keypair_data = list(self.keypair.secret())
        with open(path, 'w') as f:
            json.dump(keypair_data, f)
        print(f"Keypair saved to {path}")
    
    def sign_transaction(self, transaction_b64: str) -> str:
        """
        Sign a base64-encoded transaction from Jupiter
        
        Returns signed transaction in base64
        """
        transaction_data = b64decode(transaction_b64)
        transaction = VersionedTransaction.deserialize(transaction_data)
        
        # Sign the transaction
        signature = self.keypair.sign_message(transaction.message)
        
        # Add signature to transaction
        transaction.signatures = [signature]
        
        # Serialize and encode
        signed_data = bytes(transaction)
        return b64encode(signed_data).decode()
    
    def sign_transaction_bytes(self, transaction_b64: str) -> bytes:
        """
        Sign a base64-encoded transaction and return bytes
        
        Returns signed transaction as bytes (for Jito submission)
        """
        transaction_data = b64decode(transaction_b64)
        transaction = VersionedTransaction.deserialize(transaction_data)
        
        # Sign the transaction
        signature = self.keypair.sign_message(transaction.message)
        
        # Add signature to transaction
        transaction.signatures = [signature]
        
        # Serialize and return bytes
        return bytes(transaction)
    
    @staticmethod
    def generate_keypair() -> Keypair:
        """Generate a new keypair"""
        return Keypair()


def main():
    """Generate and save a new keypair"""
    print("Generating new burner wallet keypair...")
    signer = Signer()
    
    # Save keypair
    signer.save_keypair("burner_wallet.json")
    
    print(f"\nPublic key: {signer.public_key}")
    print(f"Private key (base64): {signer.private_key_base64}")
    print("\n⚠️  IMPORTANT: Store private key securely!")
    print("⚠️  This is a burner wallet - only fund it with small amounts!")


if __name__ == "__main__":
    main()
