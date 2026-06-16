"""
run_newsletter.py — Weekly Ortho Intel Newsletter Runner
Called by GitHub Actions every Friday at 6pm UTC.

Flow:
  1. Pull last 90 days of FDA/trials/pubmed/news from Supabase (longitudinal context)
  2. Create an Anthropic managed-agent session for Ortho Intel Newsletter Agent
  3. Send a "generate this week's newsletter" prompt with the historical context
  4. Poll until the session completes, collect the final markdown
  5. Store the draft in Supabase newsletter_drafts table
  6. Email the draft to suresh.graf@gmail.com via Resend
"""

import os
import sys
import json
import time
import datetime
import httpx

# ── Config ─────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY  = os.environ["ANTHROPIC_API_KEY"]
SUPABASE_URL       = os.environ["SUPABASE_URL"]          # e.g. https://eqahvsiukemnypkewhyu.supabase.co
SUPABASE_KEY       = os.environ["SUPABASE_SERVICE_KEY"]
RESEND_API_KEY     = os.environ.get("RESEND_API_KEY", "")
AGENT_ID           = os.environ.get("AGENT_ID", "agent_01S3KbtaxU6GWCipdJqNcrcr")
RECIPIENT_EMAIL    = os.environ.get("RECIPIENT_EMAIL", "suresh.graf@gmail.com")
FROM_EMAIL         = "newsletter@thepreferencecard.com"

ANTHROPIC_BASE     = "https://api.anthropic.com"
BETA_HEADER        = "agents-2025-05-01"

today = datetime.date.today()
issue_date = today.strftime("%Y-%m-%d")
ninety_days_ago = (today - datetime.timedelta(days=90)).isoformat()


# ── Supabase helpers ────────────────────────────────────────────────────────────
def sb_get(table: str, params: dict = None) -> list:
    """Fetch rows from a Supabase table via REST."""
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    resp = httpx.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def sb_insert(table: str, data: dict) -> dict:
    """Insert a row into a Supabase table."""
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    resp = httpx.post(url, headers=headers, json=data, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ── Fetch historical context ─────────────────────────────────────────────────────
def fetch_context() -> dict:
    print("Fetching historical data from Supabase...")

    fda = sb_get("fda_clearances", {
        "decision_date": f"gte.{ninety_days_ago}",
        "order": "decision_date.desc",
        "limit": "50",
    })
    trials = sb_get("clinical_trials", {
        "start_date": f"gte.{ninety_days_ago}",
        "order": "start_date.desc",
        "limit": "50",
    })
    pubmed = sb_get("pubmed_articles", {
        "pub_date": f"gte.{ninety_days_ago}",
        "order": "pub_date.desc",
        "limit": "30",
    })
    news = sb_get("news_items", {
        "pub_date": f"gte.{ninety_days_ago}",
        "order": "pub_date.desc",
        "limit": "30",
    })

    print(f"  FDA clearances: {len(fda)}, Trials: {len(trials)}, "
          f"PubMed: {len(pubmed)}, News: {len(news)}")

    return {"fda": fda, "trials": trials, "pubmed": pubmed, "news": news}


def build_prompt(ctx: dict) -> str:
    """Build the user message with historical context embedded."""
    lines = [
        f"Today is {issue_date}. Generate this week's issue of The Preference Card newsletter.",
        "",
        "## Historical context from your database (last 90 days)",
        "",
        f"### FDA 510(k) clearances already in DB ({len(ctx['fda'])} records)",
    ]
    for item in ctx["fda"][:20]:
        lines.append(f"- {item.get('decision_date','?')} | {item.get('device_name','?')} | {item.get('applicant','?')}")

    lines += [
        "",
        f"### Clinical trials already in DB ({len(ctx['trials'])} records)",
    ]
    for item in ctx["trials"][:20]:
        lines.append(f"- {item.get('start_date','?')} | {item.get('nct_id','?')} | {item.get('title','?')[:80]}")

    lines += [
        "",
        f"### PubMed articles already in DB ({len(ctx['pubmed'])} records)",
    ]
    for item in ctx["pubmed"][:15]:
        lines.append(f"- {item.get('pub_date','?')} | {item.get('title','?')[:80]}")

    lines += [
        "",
        f"### News items already in DB ({len(ctx['news'])} records)",
    ]
    for item in ctx["news"][:15]:
        lines.append(f"- {item.get('pub_date','?')} | {item.get('title','?')[:80]} | {item.get('source','?')}")

    lines += [
        "",
        "## Your task",
        "Using web search and web fetch, find NEW orthopedic developments from the PAST 7 DAYS "
        "that are NOT already in the database above. Then synthesize the full 5-section newsletter draft.",
        "Flag any longitudinal trends you notice (e.g., surge in a product code, repeat sponsor, "
        "indication gaining momentum).",
        "End with a clearly marked section: '## ✏️ FIELD PERSPECTIVE (Editor to complete)'",
    ]
    return "\n".join(lines)


# ── Anthropic Managed Agent session ────────────────────────────────────────────
def create_session(prompt: str) -> str:
    """Create an agent session and return the session_id."""
    url = f"{ANTHROPIC_BASE}/v1/agents/{AGENT_ID}/sessions"
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": BETA_HEADER,
        "content-type": "application/json",
    }
    body = {
        "messages": [{"role": "user", "content": prompt}],
    }
    print("Creating agent session...")
    resp = httpx.post(url, headers=headers, json=body, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    session_id = data.get("id") or data.get("session_id")
    print(f"  Session created: {session_id}")
    return session_id


def poll_session(session_id: str, max_wait: int = 900) -> str:
    """Poll session until complete; return the final text output."""
    url = f"{ANTHROPIC_BASE}/v1/agents/{AGENT_ID}/sessions/{session_id}"
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": BETA_HEADER,
    }
    start = time.time()
    poll_interval = 15

    print("Polling session for completion...")
    while time.time() - start < max_wait:
        resp = httpx.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status", "unknown")
        print(f"  Status: {status} ({int(time.time()-start)}s elapsed)")

        if status == "completed":
            # Extract text from the last assistant message
            messages = data.get("messages", [])
            for msg in reversed(messages):
                if msg.get("role") == "assistant":
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        text_parts = [c["text"] for c in content if c.get("type") == "text"]
                        return "\n\n".join(text_parts)
                    return str(content)
            return data.get("output", "")

        if status in ("failed", "error", "cancelled"):
            raise RuntimeError(f"Session ended with status: {status}\n{json.dumps(data, indent=2)}")

        time.sleep(poll_interval)

    raise TimeoutError(f"Session did not complete within {max_wait}s")


# ── Save to Supabase ────────────────────────────────────────────────────────────
def save_draft(draft: str, ctx: dict):
    print("Saving draft to Supabase newsletter_drafts...")
    row = {
        "issue_date": issue_date,
        "draft_markdown": draft,
        "fda_count": len(ctx["fda"]),
        "trials_count": len(ctx["trials"]),
        "pubmed_count": len(ctx["pubmed"]),
        "news_count": len(ctx["news"]),
    }
    result = sb_insert("newsletter_drafts", row)
    print(f"  Saved with id: {result[0].get('id') if result else '?'}")


# ── Email via Resend ────────────────────────────────────────────────────────────
def send_email(draft: str):
    if not RESEND_API_KEY:
        print("RESEND_API_KEY not set — skipping email delivery.")
        return

    print(f"Sending draft email to {RECIPIENT_EMAIL}...")

    # Convert markdown to simple HTML (basic conversion)
    html = draft.replace("\n\n", "</p><p>").replace("\n", "<br>")
    html = f"<p>{html}</p>"
    html = html.replace("## ", "<h2>").replace("<h2>", "<h2>").replace("\n</p>", "</h2><p>")

    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": FROM_EMAIL,
            "to": [RECIPIENT_EMAIL],
            "subject": f"The Preference Card — Draft for {issue_date}",
            "text": draft,
            "html": f"<pre style='font-family:sans-serif;white-space:pre-wrap'>{draft}</pre>",
        },
        timeout=30,
    )
    if resp.status_code in (200, 201):
        print(f"  Email sent! Message id: {resp.json().get('id')}")
    else:
        print(f"  Email warning: {resp.status_code} {resp.text}")


# ── Main ────────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*60}")
    print(f"The Preference Card — Weekly Newsletter Run")
    print(f"Issue date: {issue_date}")
    print(f"{'='*60}\n")

    # 1. Fetch historical context
    ctx = fetch_context()

    # 2. Build prompt
    prompt = build_prompt(ctx)
    print(f"\nPrompt length: {len(prompt)} chars\n")

    # 3. Create agent session
    session_id = create_session(prompt)

    # 4. Poll until done
    draft = poll_session(session_id)
    print(f"\nDraft received: {len(draft)} chars\n")
    print("--- DRAFT PREVIEW (first 500 chars) ---")
    print(draft[:500])
    print("...\n")

    # 5. Save to Supabase
    save_draft(draft, ctx)

    # 6. Email
    send_email(draft)

    print("\n✅ Newsletter run complete!")


if __name__ == "__main__":
    main()
