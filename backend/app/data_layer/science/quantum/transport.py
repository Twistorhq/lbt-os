# Prepared by Twistor Holdings LLC
"""TW-838 transports: how the poller reaches a vendor's device telemetry.

A transport returns BackendPayload objects whose `properties` dict is in
the Qiskit BackendProperties.to_dict() shape (the shape IBM's own client
builds from the platform API response), so the normalizer has exactly one
input contract regardless of vendor plumbing.

IBMCloudTransport targets the IBM Quantum Platform REST API
(quantum.cloud.ibm.com/api/v1). The API rejects unauthenticated calls
(verified live 2026-10-30, error 1219): the account API token is read
from the IBM_QUANTUM_TOKEN environment variable at runtime, exchanged
once for an IAM access token, and never stored, logged, or repr'd.

Account basis (Angela ruling, TW-838): production accumulation runs
against an IBM Cloud Pay-As-You-Go account (CSA). The poller enforces the
Open Plan's code-testing scale separately; the transport only carries
credentials.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol

IBM_PLATFORM_BASE = "https://quantum.cloud.ibm.com/api/v1"
IBM_IAM_TOKEN_URL = "https://iam.cloud.ibm.com/identity/token"
TOKEN_ENV = "IBM_QUANTUM_TOKEN"
INSTANCE_ENV = "IBM_QUANTUM_INSTANCE_CRN"


class MissingTokenError(RuntimeError):
    """No IBM_QUANTUM_TOKEN in the environment. Fail closed, never fake."""


@dataclass
class BackendPayload:
    """One device's verbatim payloads, as fetched."""

    name: str
    configuration: dict[str, Any]
    properties: dict[str, Any]
    status: dict[str, Any] = field(default_factory=dict)


class QuantumTransport(Protocol):
    """Vendor device-telemetry transport."""

    def list_backends(self) -> list[str]: ...

    def fetch_backend(self, name: str) -> BackendPayload: ...


class FixtureTransport:
    """In-memory transport for tests and offline code-testing runs."""

    def __init__(self, payloads: dict[str, BackendPayload]) -> None:
        self._payloads = dict(payloads)

    def list_backends(self) -> list[str]:
        return sorted(self._payloads)

    def fetch_backend(self, name: str) -> BackendPayload:
        return self._payloads[name]


class IBMCloudTransport:
    """IBM Quantum Platform REST transport (token via environment only)."""

    def __init__(
        self,
        token: str,
        session: Any = None,
        instance_crn: str | None = None,
        base_url: str = IBM_PLATFORM_BASE,
    ) -> None:
        if not token:
            raise MissingTokenError(
                f"{TOKEN_ENV} is empty: the IBM Quantum Platform API requires "
                "an account token (verified live, error 1219). Production "
                "accumulation runs on the IBM Cloud Pay-As-You-Go account "
                "Justynn creates; set the token in the environment at "
                "runtime, never in code or in the repo."
            )
        self._token = token
        self._instance_crn = instance_crn
        self._base_url = base_url.rstrip("/")
        self._access_token: str | None = None
        if session is None:
            import httpx

            session = httpx.Client(timeout=60.0)
        self._session = session

    def __repr__(self) -> str:  # token hygiene: never repr the credential
        return (
            f"IBMCloudTransport(base_url={self._base_url!r}, "
            f"instance_crn={'set' if self._instance_crn else 'unset'}, "
            "token=<redacted>)"
        )

    @classmethod
    def from_env(cls, session: Any = None) -> "IBMCloudTransport":
        token = os.environ.get(TOKEN_ENV)
        if not token:
            raise MissingTokenError(
                f"{TOKEN_ENV} is not set: the IBM Quantum Platform API "
                "requires an account token (verified live, error 1219). "
                "Production accumulation runs on the IBM Cloud "
                "Pay-As-You-Go account Justynn creates; export the token "
                "in the environment at runtime only."
            )
        return cls(
            token=token,
            session=session,
            instance_crn=os.environ.get(INSTANCE_ENV),
        )

    # -- auth ---------------------------------------------------------------
    def _access(self) -> str:
        if self._access_token is None:
            resp = self._session.post(
                IBM_IAM_TOKEN_URL,
                data={
                    "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
                    "apikey": self._token,
                },
                timeout=30,
            )
            resp.raise_for_status()
            self._access_token = resp.json()["access_token"]
        return self._access_token

    def _headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._access()}"}
        if self._instance_crn:
            headers["Service-CRN"] = self._instance_crn
        return headers

    # -- reads ---------------------------------------------------------------
    def list_backends(self) -> list[str]:
        resp = self._session.get(
            f"{self._base_url}/backends", headers=self._headers(), timeout=30
        )
        resp.raise_for_status()
        body = resp.json()
        items = body if isinstance(body, list) else body.get("backends", [])
        names = []
        for item in items:
            if isinstance(item, dict) and item.get("name"):
                names.append(item["name"])
            elif isinstance(item, str):
                names.append(item)
        return sorted(names)

    def fetch_backend(self, name: str) -> BackendPayload:
        resp = self._session.get(
            f"{self._base_url}/backends/{name}",
            headers=self._headers(),
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
        properties = body.get("properties")
        if properties is None and "qubits" in body:
            # Some surfaces return the properties document directly.
            properties = body
        return BackendPayload(
            name=body.get("name", name),
            configuration=body.get("configuration", {}),
            properties=properties or {},
            status=body.get("status", {}),
        )
