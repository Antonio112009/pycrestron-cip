"""Exceptions raised by :class:`pycrestron_cip.CipClient`."""
from __future__ import annotations


class CipError(Exception):
    """Base class for all errors of this library."""


class CipConnectionError(CipError):
    """The processor could not be reached or closed the connection."""


class CipTimeoutError(CipError, TimeoutError):
    """The processor did not answer in time (connect, registration or sync)."""


class IpidNotDefinedError(CipError):
    """The processor has no panel with this IP ID in its program (or is still booting)."""


class AuthError(CipError):
    """The processor rejected the username/password."""


class NotConnectedError(CipError):
    """A command needs a live, synced connection."""
