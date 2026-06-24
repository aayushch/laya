# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Pydantic models for repository configuration."""

from pydantic import BaseModel, Field


class RepoConfig(BaseModel):
    """A single configured local repository."""

    name: str
    path: str
    platform: str = ""  # bitbucket | github | gitlab | ""
    remote_id: str = ""  # e.g., "team/payments-service"
    host: str = ""  # git remote host; "" or bitbucket.org/github.com ⇒ cloud, else self-hosted


class ReposConfig(BaseModel):
    """repos.json structure."""

    repos: list[RepoConfig] = Field(default_factory=list)
