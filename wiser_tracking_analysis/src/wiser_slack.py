"""
wiser_slack.py
==============
Minimal, dependency-free Slack notifier for the WISER pipeline.

Reads the same Slack credential file the recorder QC scripts use
(``E:\\recording_qc\\overexposure.config.psd1`` — a PowerShell data file kept
OUT of git). Only two things are needed from it: the bot token and a list of
alert destinations. This module parses those with small regexes rather than a
full PowerShell parser, so it stays stdlib-only (urllib + json + re) and never
imports pandas/numpy — cheap to call from the hourly occupancy task on the
live field PC.

Destinations may be Slack channel ids (``C...``) or user ids (``U...``). A user
id is resolved to a DM channel via ``conversations.open`` first, matching the
recorder scripts. All network calls are best-effort: failures are printed and
swallowed, never raised, so a Slack outage can never break plotting/QC.

Config keys honoured (first present wins for the destination list):
    SlackBotToken        (required)  'xoxb-...'
    WiserAlertChannels   (optional)  @( 'Cxxxx', 'Uxxxx' )   # WISER-specific
    SlackChannels        (fallback)  @( ... )                # shared default
"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

DEFAULT_SLACK_CONFIG = Path(r"E:\recording_qc\overexposure.config.psd1")
_SLACK_POST = "https://slack.com/api/chat.postMessage"
_SLACK_OPEN = "https://slack.com/api/conversations.open"
_TIMEOUT_S = 10


# ---------------------------------------------------------------------------
# Config parsing (targeted regex, not a full PowerShell parser)
# ---------------------------------------------------------------------------

def _extract_quoted(block: str) -> list[str]:
    """All single/double-quoted tokens in *block*, dropping PS '#' comments."""
    out: list[str] = []
    for line in block.splitlines():
        line = line.split("#", 1)[0]          # strip trailing PS comment
        out.extend(re.findall(r"'([^']*)'|\"([^\"]*)\"", line))
    # findall with two groups yields tuples; keep the non-empty side.
    return [a or b for (a, b) in out if (a or b)]


def _extract_array(text: str, key: str) -> list[str]:
    """Return the string ids inside ``<key> = @( ... )``; [] if absent."""
    m = re.search(rf"{key}\s*=\s*@\((.*?)\)", text, re.S)
    if not m:
        return []
    return _extract_quoted(m.group(1))


def load_slack_config(path: Path | None = None) -> dict:
    """Parse the QC psd1 for a bot token + WISER alert destinations.

    Returns ``{"token": str|None, "channels": list[str], "source": str}``.
    A missing/unreadable file yields an empty (disabled) config rather than an
    error — the caller treats "no token" as "Slack disabled".
    """
    path = Path(path or DEFAULT_SLACK_CONFIG)
    if not path.exists():
        return {"token": None, "channels": [], "source": f"missing:{path}"}
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except Exception as exc:                    # unreadable -> disabled
        return {"token": None, "channels": [], "source": f"error:{exc}"}

    tm = re.search(r"SlackBotToken\s*=\s*'([^']*)'", text) \
        or re.search(r'SlackBotToken\s*=\s*"([^"]*)"', text)
    token = tm.group(1).strip() if tm else None
    if token in ("", "$null", None):
        token = None

    channels = _extract_array(text, "WiserAlertChannels") \
        or _extract_array(text, "SlackChannels")
    # Battery alerts are DM-only by request: prefer an explicit WiserBatteryChannels
    # key, else the user-id (DM) subset of the normal list, else the whole list.
    dm_only = [c for c in channels if c.upper().startswith("U")]
    battery_channels = _extract_array(text, "WiserBatteryChannels") \
        or dm_only or channels
    return {"token": token, "channels": channels,
            "battery_channels": battery_channels, "source": str(path)}


# ---------------------------------------------------------------------------
# HTTP (stdlib urllib; never raises to the caller)
# ---------------------------------------------------------------------------

def _post_json(url: str, token: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _resolve_channel_id(token: str, dest: str) -> str | None:
    """User id -> DM channel via conversations.open; channel id passes through."""
    if not dest.upper().startswith("U"):
        return dest
    try:
        r = _post_json(_SLACK_OPEN, token, {"users": dest})
        if r.get("ok"):
            return r["channel"]["id"]
        print(f"  [slack] conversations.open({dest}) failed: {r.get('error')}")
    except Exception as exc:
        print(f"  [slack] conversations.open({dest}) error: {exc}")
    return None


def send_slack_text(text: str, config: dict | None = None,
                    path: Path | None = None) -> int:
    """Post *text* to every configured WISER destination. Best-effort.

    Returns the number of destinations that accepted the message (0 if Slack is
    disabled/misconfigured or every send failed). Never raises.
    """
    cfg = config if config is not None else load_slack_config(path)
    token = cfg.get("token")
    channels = cfg.get("channels") or []
    if not token or not channels:
        print(f"  [slack] disabled (token={'set' if token else 'none'}, "
              f"{len(channels)} dest) source={cfg.get('source')}")
        return 0

    sent = 0
    for dest in channels:
        cid = _resolve_channel_id(token, dest)
        if not cid:
            continue
        try:
            r = _post_json(_SLACK_POST, token,
                           {"channel": cid, "text": text, "mrkdwn": True})
            if r.get("ok"):
                sent += 1
            else:
                print(f"  [slack] chat.postMessage({dest}) failed: {r.get('error')}")
        except Exception as exc:
            print(f"  [slack] chat.postMessage({dest}) error: {exc}")
    return sent
