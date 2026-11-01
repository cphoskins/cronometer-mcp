"""Drift check: do the RPC templates still match Cronometer's live policy?

A GWT type signature is a checksum of the class's serialized fields, so a
field change on Cronometer's side (issue #13) shows up here as a missing
signature before any user hits it. Run with ``pytest -m drift``.
"""

import re

import pytest
import requests

from cronometer_mcp import client

# Templates nothing calls any more; their stale types are expected.
DEAD_TEMPLATES = {"GWT_FIND_FOODS"}

SIGNATURE_RE = re.compile(r"[\w.$\[;]+/\d{6,}")


def template_signatures() -> dict[str, set[str]]:
    """Type signatures used by each live GWT_* RPC template in client.py."""
    return {
        name: set(SIGNATURE_RE.findall(value))
        for name, value in vars(client).items()
        if name.startswith("GWT_")
        and isinstance(value, str)
        and name not in DEAD_TEMPLATES
        and SIGNATURE_RE.search(value)
    }


def policy_signatures(policy_text: str) -> set[str]:
    """Signatures listed in a GWT .gwt.rpc serialization policy file."""
    sigs = set()
    for line in policy_text.splitlines():
        cols = [c.strip() for c in line.split(",")]
        if len(cols) >= 7:
            sigs.add(cols[5])
    return sigs


def missing_signatures(
    templates: dict[str, set[str]], policy: set[str]
) -> dict[str, set[str]]:
    """Template name -> signatures the policy no longer accepts."""
    assert templates and policy, "empty scan: the check would pass without checking"
    return {
        name: sigs - policy for name, sigs in templates.items() if sigs - policy
    }


def fetch_live_policy() -> str:
    """Fetch the policy the same way the client discovers its hashes."""
    s = requests.Session()
    s.headers.update({"User-Agent": "cronometer-mcp/0.1"})
    nocache = s.get(client.GWT_NOCACHE_JS_URL, timeout=30)
    nocache.raise_for_status()
    perm = re.search(r"='([A-F0-9]{32})'", nocache.text)
    assert perm, "permutation hash not found in nocache.js: discovery is broken"

    cache = s.get(
        client.GWT_CACHE_JS_URL.replace("{permutation}", perm.group(1)), timeout=60
    )
    cache.raise_for_status()
    header = re.search(r"'app','([A-F0-9]{32})'", cache.text)
    assert header, "GWT header not found in cache.js: discovery is broken"

    policy = s.get(f"{client.DEFAULT_GWT_MODULE_BASE}{header.group(1)}.gwt.rpc",
                   timeout=30)
    policy.raise_for_status()
    return policy.text


def test_missing_signatures_names_changed_type():
    """Offline: the old Serving signature from issue #13 is reported by name."""
    templates = {"GWT_UPDATE_DIARY": {
        "com.cronometer.shared.entries.models.Serving/2553599101",
        "com.cronometer.shared.entries.models.Day/782579793",
    }}
    policy = policy_signatures(
        "com.cronometer.shared.entries.models.Serving, true, true, true, true, "
        "com.cronometer.shared.entries.models.Serving/2895043750, 2895043750\n"
        "com.cronometer.shared.entries.models.Day, true, true, true, true, "
        "com.cronometer.shared.entries.models.Day/782579793, 782579793\n"
    )
    assert missing_signatures(templates, policy) == {
        "GWT_UPDATE_DIARY": {"com.cronometer.shared.entries.models.Serving/2553599101"}
    }


def test_empty_scan_cannot_pass():
    """Offline: an empty template or policy scan is an error, not a clean result."""
    with pytest.raises(AssertionError):
        missing_signatures({}, set())
    with pytest.raises(AssertionError):
        missing_signatures({}, {"a.B/123456"})
    with pytest.raises(AssertionError):
        missing_signatures({"GWT_X": {"a.B/123456"}}, set())


def test_templates_found():
    """Offline: the scan sees the write templates, so a pass means something."""
    names = template_signatures()
    assert "GWT_UPDATE_DIARY" in names
    assert "GWT_FIND_FOODS" not in names


@pytest.mark.drift
def test_templates_match_live_policy():
    missing = missing_signatures(
        template_signatures(), policy_signatures(fetch_live_policy())
    )
    assert not missing, (
        "Cronometer's serialization policy no longer accepts these types; "
        f"the class changed and the template needs re-deriving: {missing}"
    )
