"""Credit text for content narrated with ElevenLabs: a video description and a
one-line bio credit, both carrying the affiliate link.

Neither is spoken — they're pasted into the platform's own text fields, so
unlike ``spoken.py`` they're allowed digits and a literal URL.

**On masking the link.** A YouTube description or an Instagram/TikTok bio is
plain text: whatever string sits after "try it yourself:" is exactly what a
viewer sees, so there's no way to show a friendly label while hiding the
`elevenlabs.io` domain behind it without an actual redirect (a URL shortener
or a vanity domain the owner controls). This module can't stand one up
itself — it has no network access and creating one needs an account only the
owner holds. What it does instead is resolve the link from one place
(``$ELEVENLABS_AFFILIATE_LINK``, falling back to the raw affiliate URL), so
swapping in a masked link later is a one-time environment change rather than
an edit to every script and description this tool has ever produced.
"""

from __future__ import annotations

import os
from datetime import date

from .script import ScriptOptions

_DEFAULT_LINK = "https://try.elevenlabs.io/ypibh8n44scf"
_ENV_VAR = "ELEVENLABS_AFFILIATE_LINK"

VOICE_CREDIT = "Voice generated with ElevenLabs"


def affiliate_link(override: str | None = None) -> str:
    """Resolve the credit link: ``override``, then $ELEVENLABS_AFFILIATE_LINK,
    then the raw affiliate URL, in that order."""
    if override:
        return override
    return os.environ.get(_ENV_VAR) or _DEFAULT_LINK


def video_description(
    session_date: date, options: ScriptOptions, link: str | None = None
) -> str:
    """A YouTube/long-form description for an episode narrated with this link."""
    resolved = affiliate_link(link)
    return (
        f"{options.show} — {session_date.isoformat()}\n\n"
        f"Hosted by {options.anchor}. Markets, reported straight, in a voice "
        "that isn't.\n\n"
        f"\U0001f399️ {VOICE_CREDIT} — try it yourself: {resolved}\n\n"
        "Nothing here is financial advice.\n"
    )


def bio_line(link: str | None = None) -> str:
    """One line for a link-in-bio platform (Instagram, TikTok) with a single
    clickable slot."""
    return f"\U0001f399️ {VOICE_CREDIT} → {affiliate_link(link)}"
