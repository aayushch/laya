"""Gmail-specific payload normalization, validation, and event-derived identifiers."""

from __future__ import annotations


def identifiers_from_event(
    action_type: str,
    event_id: str | None,
    content_metadata: dict,
    event_row: dict,
    self_emails: set[str] | None = None,
) -> dict:
    """Derive Gmail identifiers from the event.

    - ``gmail_id`` is the message id encoded in the event_id.
    - ``to`` is the original sender (``actor_email``) for replies/forwards,
      unless the sender is the Laya user themselves (self-reply guard).
    - ``thread_id`` comes from ``content_metadata['gmail_thread_id']``.
    - ``in_reply_to`` / ``references`` come from the original message's
      RFC 2822 ``Message-Id`` / ``References`` headers, captured at
      ingestion.  Gmail's API requires these headers in addition to
      ``threadId`` for a reply to be threaded into the original conversation;
      without them Gmail treats the send as a new thread.
    - ``original_subject`` is the event's subject_title, surfaced so
      ``normalize_payload`` can enforce the ``Re: <original>`` form Gmail
      requires for threading.
    """
    ids: dict = {}
    meta = content_metadata or {}

    if event_id and event_id.startswith("evt_gmail_"):
        ids["gmail_id"] = event_id[len("evt_gmail_"):]

    thread_id = meta.get("gmail_thread_id")
    if thread_id:
        ids["thread_id"] = thread_id

    # For replies (send_email with a thread_id), populate RFC 2822 threading
    # headers.  Gmail requires In-Reply-To and References to chain messages
    # into a conversation — threadId alone is necessary but not sufficient.
    if action_type == "send_email" and thread_id:
        msg_id = (meta.get("gmail_message_id_header") or "").strip()
        existing_refs = (meta.get("gmail_references_header") or "").strip()
        if msg_id:
            ids["in_reply_to"] = msg_id
            # References chains all prior Message-Ids.  Append the current
            # message-id to any existing References header so the chain grows.
            if existing_refs:
                ids["references"] = f"{existing_refs} {msg_id}"
            else:
                ids["references"] = msg_id

        # Surface the original subject so normalize_payload can enforce
        # the Re:-prefixed form Gmail matches against for threading.
        original_subject = (event_row or {}).get("subject_title")
        if original_subject:
            ids["original_subject"] = original_subject

    # For replies, default "to" to the original sender.  Exclude "forward" —
    # the recipient of a forward is a new person chosen by the user/LLM.
    if action_type == "send_email":
        actor_email = (event_row or {}).get("actor_email")
        if actor_email:
            if not self_emails or actor_email.lower() not in self_emails:
                ids["to"] = actor_email

    return ids


def normalize_payload(action_type: str, payload: dict) -> dict:
    """Normalize Gmail executor payload fields."""
    p = dict(payload)

    # LLMs sometimes emit the generic "message_id" instead of "gmail_id".
    if action_type in ("archive", "star", "unstar", "mark_read") and not p.get("gmail_id"):
        msg_id = p.pop("message_id", None)
        if msg_id:
            p["gmail_id"] = msg_id

    # Ensure "body" exists — LLMs use various key names
    if "body" not in p:
        p["body"] = (
            p.pop("message", None)
            or p.pop("content", None)
            or p.pop("text", None)
            or p.pop("reply_body", None)
            or p.pop("email_body", None)
            or p.pop("reply", None)
            or ""
        )

    # Enforce reply subject.  Gmail's threading API refuses to link a message
    # to an existing thread unless the Subject matches (modulo a Re: prefix)
    # — so when we have the original subject from the event, we override any
    # LLM-supplied subject rather than trusting it.  The LLM is free to
    # express intent in the body; the subject is a threading key, not creative
    # surface.
    if action_type == "send_email" and p.get("thread_id"):
        original = (p.pop("original_subject", None) or "").strip()
        if original:
            base = original[4:].strip() if original.lower().startswith("re:") else original
            p["subject"] = f"Re: {base}"
        else:
            # No original — fall back to prefixing whatever the LLM emitted.
            subject = p.get("subject", "")
            if subject and not subject.lower().startswith("re:"):
                p["subject"] = f"Re: {subject}"

    # Forward: ensure Fwd: prefix
    if action_type == "forward":
        subject = p.get("subject", "")
        if subject and not subject.lower().startswith("fwd:"):
            p["subject"] = f"Fwd: {subject}"

    return p


def build_api_payload(action_type: str, payload: dict) -> dict:
    """Convert normalized payload to Gmail API-ready format.

    For send_email, builds a raw RFC 2822 MIME message and base64url-encodes
    it so the n8n workflow can pass it straight to gmail.googleapis.com without
    any expression-side string manipulation.
    """
    if action_type != "send_email":
        return payload

    import base64

    to = payload.get("to", "")
    subject = payload.get("subject", "")
    body_text = payload.get("body", "")
    in_reply_to = payload.get("in_reply_to", "")
    references = payload.get("references", "")
    thread_id = payload.get("thread_id", "")

    headers = [
        f"To: {to}",
        f"Subject: {subject}",
    ]
    if in_reply_to:
        headers.append(f"In-Reply-To: {in_reply_to}")
    if references:
        headers.append(f"References: {references}")
    headers.extend([
        "MIME-Version: 1.0",
        'Content-Type: text/plain; charset="UTF-8"',
        "Content-Transfer-Encoding: 7bit",
        "",
        body_text,
    ])

    mime = "\r\n".join(headers)
    raw = base64.urlsafe_b64encode(mime.encode("utf-8")).decode("ascii").rstrip("=")

    result: dict = {"raw": raw}
    if thread_id:
        result["threadId"] = thread_id
    return result


def validate_payload(action_type: str, payload: dict) -> list[str]:
    """Return list of validation errors (empty if valid)."""
    errors = []

    if action_type == "send_email":
        if not payload.get("to"):
            errors.append("Missing 'to' recipient address")
        if not payload.get("body"):
            errors.append("Missing email 'body'")

    elif action_type == "forward":
        if not payload.get("to"):
            errors.append("Missing 'to' forward recipient")

    elif action_type in ("archive", "star", "unstar", "mark_read"):
        if not payload.get("gmail_id"):
            errors.append(f"Missing 'gmail_id' for {action_type}")

    return errors
