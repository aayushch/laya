# Copyright 2026 Aayush Chawla
# SPDX-License-Identifier: Apache-2.0

"""Notion-specific payload normalization, validation, and event-derived identifiers."""

from __future__ import annotations

from typing import Any

from laya.egress.models import EgressCapability
from laya.egress.platforms.base import Platform


class NotionPlatform(Platform):
    name = "notion"
    platform_hint = "a Notion page or comment"
    chapter_default = "Note"
    source_ref_config = {"use_title": True}
    compose_guidance = (
        "You are composing a NOTION PAGE or COMMENT. Field requirements:\n"
        "- 'title': page title.\n"
        "- 'body': page or comment body text."
    )
    polish_guidance = (
        "Notion page — clear and structured; preserve any @mentions and links."
    )

    capabilities = [
        EgressCapability(
            action_type="create_page",
            label="Create Page",
            requires_fields=["parent_id", "title"],
            optional_fields=["parent_type", "properties", "children"],
            content_fields=["title"],
            optional_content_fields=["properties", "children"],
            description="Create a new Notion page inside a database or parent page.",
            summary_template="Create Notion page: '{title}'",
            impact="medium",
        ),
        EgressCapability(
            action_type="append_block",
            label="Append Block",
            requires_fields=["page_id", "text"],
            optional_fields=["block_type"],
            content_fields=["text"],
            optional_content_fields=["block_type"],
            description="Append a text block to a Notion page.",
            summary_template="Append block to page {page_id}",
            impact="medium",
        ),
        EgressCapability(
            action_type="update_page_property",
            label="Update Property",
            requires_fields=["page_id", "property_name", "property_value"],
            optional_fields=["property_type"],
            content_fields=["property_value"],
            optional_content_fields=["property_type"],
            description="Update a single property on a Notion page (status, tags, etc.).",
            summary_template="Update '{property_name}' on page {page_id}",
            impact="medium",
        ),
        EgressCapability(
            action_type="archive_page",
            label="Archive Page",
            requires_fields=["page_id"],
            description="Archive a Notion page (soft delete).",
            summary_template="Archive page {page_id}",
            warnings=["This will archive the Notion page. It can be restored from Notion's trash."],
            impact="medium",
        ),
        EgressCapability(
            action_type="add_comment",
            label="Add Comment",
            requires_fields=["page_id", "comment"],
            content_fields=["comment"],
            description="Add a comment to a Notion page.",
            summary_template="Comment on page {page_id}",
            impact="medium",
        ),
    ]

    def identifiers_from_event(
        self,
        action_type: str,
        event_id: str | None,
        content_metadata: dict,
        event_row: dict,
        self_emails: set[str] | None = None,
    ) -> dict:
        """Derive Notion identifiers from the source event.

        ``page_id`` is the Notion page UUID captured as ``subject_id`` by the
        ingestion workflow.  ``parent_id`` / ``parent_type`` come from
        ``content_metadata`` and cover the ``create_page`` path where the target
        is a database or parent page.
        """
        ids: dict = {}
        subject_id = (event_row or {}).get("subject_id")
        if subject_id:
            ids["page_id"] = subject_id

        meta = content_metadata or {}
        parent_id = meta.get("notion_parent_id")
        if parent_id:
            ids["parent_id"] = parent_id
        parent_type = meta.get("notion_parent_type")
        if parent_type:
            ids["parent_type"] = parent_type

        return ids

    def normalize_payload(self, action_type: str, payload: dict) -> dict:
        """Normalize Notion executor payload fields into the shape the n8n
        workflow expects.
        """
        p = dict(payload)

        # page_id: accept camelCase / alt keys
        if "page_id" not in p:
            p["page_id"] = (
                p.pop("pageId", None)
                or p.pop("pageID", None)
                or p.pop("id", None)
                or ""
            )

        # parent_id for create_page: accept databaseId / parent alt keys
        if action_type == "create_page" and "parent_id" not in p:
            p["parent_id"] = (
                p.pop("parentId", None)
                or p.pop("database_id", None)
                or p.pop("databaseId", None)
                or p.pop("parent", None)
                or ""
            )

        # parent_type defaults to "database" — most create_page flows target a DB.
        # Notion's REST API needs {database_id} vs {page_id} in the parent object,
        # so the executor branches on this flag to pick the right shape.
        if action_type == "create_page" and not p.get("parent_type"):
            p["parent_type"] = "database"

        # title: accept 'name' / 'summary' aliases
        if action_type == "create_page" and "title" not in p:
            p["title"] = p.pop("name", None) or p.pop("summary", None) or ""

        # append_block text: accept body/content/message aliases
        if action_type == "append_block" and "text" not in p:
            p["text"] = (
                p.pop("body", None)
                or p.pop("content", None)
                or p.pop("message", None)
                or ""
            )
        # Default block type to paragraph for append_block.
        if action_type == "append_block" and not p.get("block_type"):
            p["block_type"] = "paragraph"

        # add_comment: accept body/text/message aliases
        if action_type == "add_comment" and "comment" not in p:
            p["comment"] = (
                p.pop("body", None)
                or p.pop("text", None)
                or p.pop("message", None)
                or ""
            )

        # update_page_property: coerce (property_name, property_value) into a
        # Notion-shaped `properties` object so the executor can forward it
        # verbatim.  This keeps property-type knowledge out of the n8n workflow.
        if action_type == "update_page_property":
            if "properties" not in p:
                name = p.get("property_name")
                value = p.get("property_value")
                ptype = p.get("property_type") or self._infer_property_type(value)
                if name:
                    p["properties"] = {name: self._shape_property(ptype, value)}

        return p

    def validate_payload(self, action_type: str, payload: dict) -> list[str]:
        """Return list of validation errors for a Notion payload."""
        errors: list[str] = []

        if action_type in ("append_block", "update_page_property", "archive_page", "add_comment"):
            if not payload.get("page_id"):
                errors.append("Missing 'page_id' (Notion page UUID)")

        if action_type == "create_page":
            if not payload.get("parent_id"):
                errors.append("Missing 'parent_id' (Notion database or page ID)")
            if not payload.get("title"):
                errors.append("Missing page 'title'")

        if action_type == "append_block":
            if not payload.get("text"):
                errors.append("Missing 'text' for block content")

        if action_type == "update_page_property":
            if not payload.get("property_name"):
                errors.append("Missing 'property_name'")
            if "property_value" not in payload and "properties" not in payload:
                errors.append("Missing 'property_value'")

        if action_type == "add_comment":
            if not payload.get("comment"):
                errors.append("Missing 'comment' body")

        return errors

    # -----------------------------------------------------------------------
    # Notion property shaping helpers
    # -----------------------------------------------------------------------

    def _infer_property_type(self, value: Any) -> str:
        """Guess the Notion property type from a Python value."""
        if isinstance(value, bool):
            return "checkbox"
        if isinstance(value, (int, float)):
            return "number"
        if isinstance(value, list):
            # Assume multi-select of strings
            return "multi_select"
        return "rich_text"

    def _shape_property(self, ptype: str, value: Any) -> dict:
        """Shape a Python value into the Notion property JSON for the given type.

        Covers the common cases (title, rich_text, select, multi_select, checkbox,
        number, date, status, people, url, email, phone_number).  Unknown types
        fall through as rich_text so Notion can surface a clearer error than a
        silent drop would.
        """
        t = (ptype or "").lower()

        if t == "title":
            return {"title": [{"type": "text", "text": {"content": str(value or "")}}]}
        if t in ("rich_text", "text"):
            return {"rich_text": [{"type": "text", "text": {"content": str(value or "")}}]}
        if t == "select":
            return {"select": {"name": str(value)} if value else None}
        if t == "multi_select":
            items = value if isinstance(value, list) else [value]
            return {"multi_select": [{"name": str(v)} for v in items if v]}
        if t == "status":
            return {"status": {"name": str(value)} if value else None}
        if t == "checkbox":
            return {"checkbox": bool(value)}
        if t == "number":
            return {"number": float(value) if value is not None else None}
        if t == "date":
            # Accept either a bare ISO string or {start, end} dict.
            if isinstance(value, dict):
                return {"date": value}
            return {"date": {"start": str(value)} if value else None}
        if t == "people":
            items = value if isinstance(value, list) else [value]
            return {"people": [{"object": "user", "id": str(v)} for v in items if v]}
        if t == "url":
            return {"url": str(value) if value else None}
        if t == "email":
            return {"email": str(value) if value else None}
        if t == "phone_number":
            return {"phone_number": str(value) if value else None}

        # Unknown / unsupported type — fall back to rich_text
        return {"rich_text": [{"type": "text", "text": {"content": str(value or "")}}]}


PLATFORM = NotionPlatform()
