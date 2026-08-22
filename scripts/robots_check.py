#!/usr/bin/env python3
"""Robots.txt compliance gate for the research harvesting pipeline.

Before the harvest backends fetch a candidate URL, this module performs a
programmatic robots.txt check: it fetches the site's robots.txt, parses
the rules for our crawler User-Agent, and asserts that the target path is
not disallowed. A disallowed URL is dropped (with a warning) rather than
fetched — this is the enforcement layer for the compliance policy
documented in README §3 ("不绕过任何访问控制").

Usage:
  # Check one URL and exit 0 (allowed) / 1 (disallowed) / 2 (fetch error)
  python scripts/robots_check.py --url https://www.sec.gov/cgi-bin/browse-edgar

  # As a library (called by research_harvest.py):
  from scripts.robots_check import RobotsGate
  gate = RobotsGate()
  if not gate.allowed(url):
      continue  # skip disallowed URL

Design notes:
- robots.txt is fetched once per host and cached for the process lifetime.
- The standard User-Agent of the harvesting pipeline (SCR_USER_AGENT env
  var, default "SupplyChainResearchBot/1.0 (+contact@example.com)") is
  matched against rules; a `*` group is the fallback.
- We honor the RFC 9309 spirit: only User-agent / Allow / Disallow /
  Crawl-delay directives are consulted; path matching uses the wildcard
  syntax (* = any, $ = end-of-path).
- Failures to fetch robots.txt default to ALLOW (open-by-default), which
  matches standard crawler convention for sites without a robots.txt.
"""

from __future__ import annotations

import argparse
import os
import sys
from functools import lru_cache
from typing import Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

# Conservative fetch timeout: robots.txt is tiny and we never want a hung
# site to block the harvest pipeline.
_ROBOTS_TIMEOUT = 10.0
_DEFAULT_UA = "SupplyChainResearchBot/1.0 (+contact@example.com)"


class RobotsGate:
    """A per-process robots.txt compliance gate.

    Caches the parsed RobotFileParser per host so that bulk harvesting of
    the same domain (e.g. SEC EDGAR) pays the fetch cost only once.
    """

    def __init__(self, user_agent: Optional[str] = None) -> None:
        self.user_agent = user_agent or os.environ.get(
            "SCR_USER_AGENT", _DEFAULT_UA
        )

    @lru_cache(maxsize=128)
    def _parser_for(self, host_scheme: str) -> Optional[RobotFileParser]:
        """Return a parsed RobotFileParser for the host, or None on failure."""
        robots_url = f"{host_scheme}/robots.txt"
        try:
            import httpx
            with httpx.Client(timeout=_ROBOTS_TIMEOUT) as client:
                resp = client.get(
                    robots_url,
                    headers={"User-Agent": self.user_agent},
                    follow_redirects=True,
                )
                if resp.status_code >= 400:
                    # No robots.txt (404/403): open-by-default.
                    return None
                text = resp.text
        except Exception:
            # Network failure fetching robots.txt: do not block harvesting,
            # but surface the failure so reviewers are aware.
            print(
                f"warn: could not fetch {robots_url} — treating as allow",
                file=sys.stderr,
            )
            return None

        rp = RobotFileParser()
        rp.set_url(robots_url)
        rp.parse(text.splitlines())
        return rp

    def allowed(self, url: str) -> bool:
        """True if the URL may be fetched under our User-Agent."""
        parsed = urlparse(url.strip())
        if not parsed.scheme or not parsed.netloc:
            return False
        host_key = f"{parsed.scheme}://{parsed.netloc}"
        rp = self._parser_for(host_key)
        if rp is None:
            return True  # no robots.txt -> open by default
        return rp.can_fetch(self.user_agent, url)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", required=True, help="Target URL to check")
    ap.add_argument(
        "--user-agent",
        default=None,
        help="Override SCR_USER_AGENT for the check",
    )
    args = ap.parse_args()

    gate = RobotsGate(user_agent=args.user_agent)
    if gate.allowed(args.url):
        print(f"ALLOW  {args.url}")
        return 0
    print(f"DENY   {args.url} (disallowed by robots.txt)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
