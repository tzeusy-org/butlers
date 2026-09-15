"""Synthetic authenticators for owner-auth verifier and store contracts."""

import hashlib
import json
import secrets
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from fido2.cose import ES256, RS256
from fido2.webauthn import AttestationObject, AttestedCredentialData, AuthenticatorData

from butlers.api.owner_auth.verifier import encode

ORIGIN = "https://butlers.example.test"
RP = "butlers.example.test"


@dataclass
class Passkey:
    key: object
    credential_id: bytes
    be: bool = True
    counter: int = 0

    @classmethod
    def create(cls, *, rsa_key=False, be=True):
        key = (
            rsa.generate_private_key(public_exponent=65537, key_size=2048)
            if rsa_key
            else ec.generate_private_key(ec.SECP256R1())
        )
        return cls(key, secrets.token_bytes(32), be)

    def response(
        self, options, *, register, origin=ORIGIN, rp=RP, uv=True, bs=False, user_handle=None
    ):
        public = options["publicKey"]
        client = json.dumps(
            {
                "type": "webauthn.create" if register else "webauthn.get",
                "challenge": public["challenge"],
                "origin": origin,
                "crossOrigin": False,
            }
        ).encode()
        flags = 1 | (4 if uv else 0) | (8 if self.be else 0) | (16 if bs else 0)
        if register:
            cose = (
                RS256.from_cryptography_key(self.key.public_key())
                if isinstance(self.key, rsa.RSAPrivateKey)
                else ES256.from_cryptography_key(self.key.public_key())
            )
            data = AttestedCredentialData.create(b"\0" * 16, self.credential_id, cose)
            auth = AuthenticatorData.create(
                hashlib.sha256(rp.encode()).digest(), flags | 64, self.counter, data
            )
            response = {
                "clientDataJSON": encode(client),
                "attestationObject": encode(AttestationObject.create("none", auth, {})),
                "transports": ["hybrid"],
            }
        else:
            auth = AuthenticatorData.create(
                hashlib.sha256(rp.encode()).digest(), flags, self.counter
            )
            message = bytes(auth) + hashlib.sha256(client).digest()
            signature = (
                self.key.sign(message, padding.PKCS1v15(), hashes.SHA256())
                if isinstance(self.key, rsa.RSAPrivateKey)
                else self.key.sign(message, ec.ECDSA(hashes.SHA256()))
            )
            response = {
                "clientDataJSON": encode(client),
                "authenticatorData": encode(auth),
                "signature": encode(signature),
                "userHandle": user_handle,
            }
        return {
            "id": encode(self.credential_id),
            "rawId": encode(self.credential_id),
            "type": "public-key",
            "clientExtensionResults": {},
            "response": response,
        }
