"""Explicit HTTPS acquisition of verified local plugin ZIP artifacts.

This is a transport primitive, not a PluginSource or an installer. A host
chooses the URL, trusted SHA-256, and allowed origin. Only the host may then
hand the temporary local file to LocalPluginStore.install_zip().
"""
from __future__ import annotations

import hashlib
import hmac
import re
import ssl
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

_MAX_DOWNLOAD_BYTES = 30 * 1024 * 1024  # The local Store rejects larger ZIPs.
_SHA256 = re.compile(r"[a-fA-F0-9]{64}\Z")


class PluginAcquisitionError(ValueError):
    """Rejected URL, response, or downloaded ZIP integrity."""


def _https_origin(value: str, *, origin_only: bool = False) -> str:
    """Validate an exact HTTPS origin; never normalize dubious URLs to trust."""
    if not isinstance(value, str) or not value or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        raise PluginAcquisitionError("invalid HTTPS URL")
    if "\\" in value:
        raise PluginAcquisitionError("backslashes forbidden in HTTPS URL")
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme != "https" or not parsed.netloc or not parsed.hostname:
            raise PluginAcquisitionError("HTTPS URL required")
        if parsed.username is not None or parsed.password is not None or parsed.fragment:
            raise PluginAcquisitionError("credentials/fragments forbidden in HTTPS URL")
        host = parsed.hostname
        if not host.isascii() or "%" in host:  # Use punycode for IDNs; reject IPv6 zone IDs.
            raise PluginAcquisitionError("non-ASCII or scoped HTTPS hostname")
        port = 443 if parsed.port is None else parsed.port
        if port <= 0:
            raise PluginAcquisitionError("invalid HTTPS port")
    except ValueError as exc:
        raise PluginAcquisitionError("invalid HTTPS URL") from exc
    if origin_only and (parsed.path not in ("", "/") or parsed.query):
        raise PluginAcquisitionError("allowed origins cannot contain paths or queries")
    # Preserve IPv6 delimiters in origins.
    authority = f"[{host}]" if ":" in host else host
    return f"https://{authority}" + (f":{port}" if port != 443 else "")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@contextmanager
def download_verified_zip(
    url: str,
    *,
    expected_sha256: str,
    allowed_origins: Iterable[str],
    timeout_seconds: float = 20.0,
    max_bytes: int = _MAX_DOWNLOAD_BYTES,
) -> Iterator[Path]:
    """Stream one explicitly authorized HTTPS ZIP to a temporary local file.

    A SHA-256 obtained independently from a trusted source is required. The
    requested HTTPS origin must match an explicitly allowed origin. Redirects,
    ambient proxies, credentials, oversized responses and invalid ZIPs are
    rejected. The temporary file is deleted after the context, including on
    exceptions. This function never installs or executes plugin code.
    """
    origin = _https_origin(url)
    if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
        raise PluginAcquisitionError("expected_sha256 must be 64 hexadecimal characters")
    trusted = {_https_origin(candidate, origin_only=True) for candidate in allowed_origins}
    if origin not in trusted:
        raise PluginAcquisitionError("HTTPS origin not allowed")
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1:
        raise PluginAcquisitionError("max_bytes must be a positive integer")
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= 300:
        raise PluginAcquisitionError("timeout_seconds must be in (0, 300]")

    # Explicitly bypass ambient HTTP(S)_PROXY settings. The host owns which
    # origin may be contacted, so environment proxies must not redirect it.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        _NoRedirect(),
    )
    request = urllib.request.Request(
        url, headers={"Accept": "application/zip, application/octet-stream", "Accept-Encoding": "identity"},
        method="GET",
    )
    with tempfile.TemporaryDirectory(prefix="invariant-plugin-https-") as temp_dir:
        downloaded = Path(temp_dir) / "plugin.zip"
        digest = hashlib.sha256()
        total = 0
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                if response.geturl() != url or response.status != 200:
                    raise PluginAcquisitionError("unexpected HTTPS response or redirect")
                encoding = response.headers.get("Content-Encoding", "identity").lower()
                if encoding != "identity":
                    raise PluginAcquisitionError("compressed HTTP responses not supported")
                length = response.headers.get("Content-Length")
                if length is not None:
                    try:
                        claimed = int(length)
                    except ValueError as exc:
                        raise PluginAcquisitionError("invalid HTTP content length") from exc
                    if claimed < 0 or claimed > max_bytes:
                        raise PluginAcquisitionError("ZIP exceeds download size limit")
                with downloaded.open("wb") as output:
                    while chunk := response.read(min(1024 * 1024, max_bytes + 1 - total)):
                        total += len(chunk)
                        if total > max_bytes:
                            raise PluginAcquisitionError("ZIP exceeds download size limit")
                        digest.update(chunk)
                        output.write(chunk)
                if length is not None and total != claimed:
                    raise PluginAcquisitionError("incomplete HTTPS download")
        except urllib.error.HTTPError as exc:
            # HTTPError owns the socket even when the response context was not
            # entered. Close it without reading the potentially unbounded body.
            exc.close()
            raise PluginAcquisitionError("HTTPS download rejected") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PluginAcquisitionError("HTTPS download failed") from exc
        if not hmac.compare_digest(digest.hexdigest(), expected_sha256.lower()):
            raise PluginAcquisitionError("downloaded ZIP SHA-256 mismatch")
        if not zipfile.is_zipfile(downloaded):
            raise PluginAcquisitionError("downloaded artifact is not a ZIP")
        yield downloaded
