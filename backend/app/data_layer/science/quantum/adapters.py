# Prepared by Twistor Holdings LLC
"""TW-838 vendor adapters: one interface, vendors slot in behind it.

The poller talks to QuantumTransport objects. Each vendor gets an adapter
that knows how to build its transport. IBM is live in this run. Braket is
interface-only: its poller is gated on the AWS account Justynn creates
(brief, Section 5), and its adapter raises AdapterUnavailable until then,
so the rest of the lane never special-cases a missing vendor.
"""

from __future__ import annotations

from typing import Any

from .transport import IBMCloudTransport, QuantumTransport


class AdapterUnavailable(RuntimeError):
    """The vendor's prerequisites (account, credentials) are not in place."""


class IBMAdapter:
    vendor = "ibm"
    available = True

    def transport(self, session: Any = None) -> QuantumTransport:
        return IBMCloudTransport.from_env(session=session)


class BraketAdapter:
    """Amazon Braket (IonQ / Rigetti / IQM / OQC / QuEra device properties).

    Collection carries no purpose-limit flag (Angela's read: no collection
    bar in the Braket service terms), but the build is gated on an AWS
    account Justynn creates. When it lands, this adapter builds a boto3
    Braket GetDevice transport behind the same QuantumTransport contract.
    """

    vendor = "braket"
    available = False

    def transport(self, session: Any = None) -> QuantumTransport:
        raise AdapterUnavailable(
            "Braket poller is gated on the AWS account Justynn creates "
            "(TW-838 brief, Section 5). Adapter interface only in this run."
        )


ADAPTERS = {"ibm": IBMAdapter, "braket": BraketAdapter}


def get_adapter(vendor: str):
    try:
        return ADAPTERS[vendor]()
    except KeyError:
        raise AdapterUnavailable(f"unknown quantum vendor adapter {vendor!r}")
