#!/usr/bin/env python3
"""GasBuddy HTTP layer with browser impersonation (curl_cffi).

Drop-in replacements for the urllib-based helpers in fetch_metro.py and
fetch_areas.py. A module-level session is established lazily with rotating
TLS-impersonation profiles; on a 403 the profile rotates and the session is
re-established automatically.

Proven 2026-10-01: a GitHub Actions runner reaches GasBuddy's GraphQL with
the safari17_0 profile (chrome profiles 403 from the same IP).
"""
import re
import time

from curl_cffi import requests as crequests

PROFILES = ["safari17_0", "chrome131", "chrome124", "chrome"]
ENTRY_URLS = ["https://www.gasbuddy.com/home", "https://www.gasbuddy.com/"]
ESTABLISH_BACKOFFS = [3, 5, 8, 10]

_session = None
_profile_idx = 0


def _try_profile(profile):
    """Return a cleared Cloudflare session for profile, or None."""
    for url in ENTRY_URLS:
        try:
            s = crequests.Session(impersonate=profile)
            r = s.get(url, timeout=30)
            body = r.text or ""
            if r.status_code == 200 and "window.gbcsrf" in body:
                return s
        except Exception:
            pass
        time.sleep(2)
    return None


def _establish():
    """(Re)establish the session, rotating through profiles."""
    global _session, _profile_idx
    for _ in range(len(PROFILES)):
        profile = PROFILES[_profile_idx % len(PROFILES)]
        _profile_idx += 1
        s = _try_profile(profile)
        if s is not None:
            _session = s
            return s
        time.sleep(ESTABLISH_BACKOFFS[min(_profile_idx, len(ESTABLISH_BACKOFFS) - 1)])
    raise RuntimeError("gbhttp: no impersonation profile cleared the Cloudflare wall")


def _session_or_establish():
    global _session
    if _session is None:
        _establish()
    return _session


def _rotate():
    global _session
    _session = None
    return _establish()


def http_get(url, headers=None, tries=4):
    """GET url -> decoded text. Same signature as the old urllib helper."""
    last = None
    for i in range(tries):
        try:
            s = _session_or_establish()
            r = s.get(url, headers=headers or {}, timeout=30)
            if r.status_code == 403:
                last = RuntimeError(f"HTTP 403 for {url}")
                _rotate()
                continue
            r.raise_for_status()
            return r.text
        except Exception as e:
            last = e
            try:
                _rotate()
            except Exception:
                pass
            time.sleep(2 ** i)
    raise RuntimeError(f"gbhttp GET {url} failed after {tries} tries: {last}")


def http_post_json(url, headers=None, payload=None, tries=4):
    """POST JSON payload -> parsed dict. Retries with profile rotation."""
    last = None
    for i in range(tries):
        try:
            s = _session_or_establish()
            r = s.post(url, headers=headers or {}, json=payload, timeout=30)
            if r.status_code == 403:
                last = RuntimeError(f"HTTP 403 for {url}")
                _rotate()
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            try:
                _rotate()
            except Exception:
                pass
            time.sleep(2 ** i)
    raise RuntimeError(f"gbhttp POST {url} failed after {tries} tries: {last}")


def reset():
    """Drop the cached session (tests / fresh runs)."""
    global _session
    _session = None
