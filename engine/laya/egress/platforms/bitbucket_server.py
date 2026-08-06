# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Bitbucket Server / Data Center (on-prem) egress adapter.

Mirrors the Bitbucket Cloud adapter — same capabilities, payload shape
(``workspace`` carries the Server *project key*, ``repo`` the repository slug,
matching the ``project/repo`` remote_id parsed from on-prem clone URLs) and
``bb_*`` event metadata — but its executor drives the Server REST API
(``{base_url}/rest/api/1.0/...``) instead of ``api.bitbucket.org``. The
per-connection base URL is injected into the payload by the n8n backend via
``payload_credential_fields``; it is never LLM- or user-supplied.
"""

from __future__ import annotations

import re

from laya.egress.platforms.bitbucket import BitbucketPlatform


class BitbucketServerPlatform(BitbucketPlatform):
    name = "bitbucket_server"
    platform_hint = "a Bitbucket Server PR or comment"
    event_id_pr_re = re.compile(r"^evt_bbs_pr_.+_(?P<id>\d+)_\d+$")
    # n8n backend copies the connection's server URL into the payload so the
    # executor workflow can build REST URLs for this specific instance; the
    # opt-in TLS toggle rides along so its HTTP nodes can set
    # allowUnauthorizedCerts for servers with internal-CA/self-signed certs.
    payload_credential_fields = {"server": "base_url", "allowInsecureSsl": "allow_insecure_ssl"}
    compose_guidance = (
        "You are composing a BITBUCKET SERVER PR or COMMENT. Field requirements:\n"
        "- 'repo': repository in project/repo format (project key + repo slug).\n"
        "- 'title': concise one-line PR title.\n"
        "- 'body': full body text (supports markdown)."
    )

    def normalize_payload(self, action_type: str, payload: dict) -> dict:
        had_strategy = "merge_strategy" in payload
        p = super().normalize_payload(action_type, payload)
        # Server/DC repos configure their own default merge strategy and may not
        # have Cloud's default ("squash") enabled at all — drop the inherited
        # default so the server applies the repo's configured strategy instead
        # of 409-ing on a forbidden one. An explicit choice passes through.
        if action_type == "merge_pr" and not had_strategy:
            p.pop("merge_strategy", None)
        return p


PLATFORM = BitbucketServerPlatform()
