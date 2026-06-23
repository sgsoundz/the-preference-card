"""
sales_brief_agent.py — The Preference Card: Sales Intelligence Brief
Runs after run_newsletter.py in the GitHub Actions workflow.

Reads the latest newsletter draft from Supabase, passes it to Claude
with a sales-rep-focused prompt, and emails a separate Sales Intelligence
Brief to suresh.graf@gmail.com.
"""

import os
import datetime
import httpx
import anthropic

# ── Config ──────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SUPABASE_URL      = os.environ["SUPABASE_URL"]
SUPABASE_KEY      = os.environ["SUPABASE_SERVICE_KEY"]
RESEND_API_KEY    = os.environ.get("RESEND_API_KEY", "")
RECIPIENT_EMAIL   = os.environ.get("RECIPIENT_EMAIL", "suresh.graf@gmail.com")
FROM_EMAIL        = "newsletter@thepreferencecard.com"

today = datetime.date.today()

SYSTEM_PROMPT = """You are a field intelligence analyst for The Preference Card newsletter, \
writing exclusively for orthopedic medical device sales reps and rep managers.

Your job is to take the week's newsletter draft — which is written for a broad audience of \
surgeons, hospital admins, and investors — and translate it into a tight, actionable Sales \
Intelligence Brief. This is the difference between knowing something happened and knowing \
what to do about it on Monday morning.

Your audience carries a bag. They're in the OR 3 days a week, in admin meetings 1 day, \
and on the phone with their manager the 5th. They need intelligence they can act on, not \
intelligence they have to decode.

FORMAT (follow exactly):

---
# THE PREFERENCE CARD — SALES INTELLIGENCE BRIEF
**Week of {date} | Built for Reps & Rep Managers**

---

## 🎯 REP PLAYBOOK: Top 3 Conversations This Week

[Three specific, numbered talking points a rep can walk into a room with on Monday. \
Each one includes: the insight, who to bring it to (surgeon / OR director / admin / C-suite), \
and the one-sentence opener. Be direct. "Walk into your robotics accounts and ask..." \
is more useful than "Consider discussing..."]

---

## 🔥 COMPETITIVE WATCH

[What competitors are doing this week — clearances filed, trials started, deals announced. \
Frame every item in terms of what it means for a rep: is this a threat, an opportunity, \
or noise? If nothing notable, say so — that's signal too.]

---

## 🏥 HOSPITAL & IDN SIGNALS

[M&A, funding, and market moves translated into purchasing implications. \
What should reps expect from hospital administrators? Are budgets likely to \
tighten or loosen? Are GPO contracts at risk? Is a big consolidation creating \
a window before new purchasing policies lock in?]

---

## 📋 CLINICAL CONVERSATION STARTERS

[3–5 studies from this week's literature that reps can reference with surgeons. \
For each: one sentence on the finding, one sentence on why it opens a door. \
Organized by procedure/specialty. Never ask a rep to read an abstract — give them \
the line to say.]

---

## ⚡ SPECIALTY SPOTLIGHT

[The single biggest trend from this week's data — the one a smart rep would \
build a talking track around. Go deeper here: what's the trend, who's driving it, \
what does it mean for volume, and what's the specific play for a rep in that space?]

---

## 📌 WATCH LIST FOR NEXT WEEK

[2–3 items to monitor. Trials about to report results, companies filing patterns \
that suggest an upcoming announcement, regulatory windows, or seasonal purchasing \
cycles. Give reps a reason to come back next week.]

---
*The Preference Card Sales Brief — distributed with your weekly newsletter.*
*Questions or field observations? Reply directly to this email.*
---
"""


def sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


def get_latest_draft() -> str:
    """Pull the most recently generated newsletter draft from Supabase."""
    resp = httpx.get(
        f"{SUPABASE_URL}/rest/v1/newsletter_drafts",
        headers=sb_headers(),
        params={
            "select": "draft_markdown,issue_date",
            "order": "issue_date.desc",
            "limit": "1",
        },
        timeout=30,
    )
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        raise ValueError("No newsletter drafts found in Supabase.")
    return rows[0]["draft_markdown"]


def generate_sales_brief(newsletter_draft: str) -> str:
    """Call Claude to generate the sales intelligence brief."""
    print("Generating Sales Intelligence Brief...")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system=SYSTEM_PROMPT.format(date=today.strftime("%B %d, %Y")),
        messages=[{
            "role": "user",
            "content": f"Here is this week's newsletter draft. Generate the Sales Intelligence Brief:\n\n{newsletter_draft}"
        }],
    )
    text = "".join(block.text for block in response.content if hasattr(block, "text"))
    print(f"  Brief: {len(text)} chars")
    return text


def send_sales_brief(brief: str):
    """Email the sales brief via Resend."""
    if not RESEND_API_KEY:
        print("RESEND_API_KEY not set — skipping email.")
        return
    print(f"Emailing Sales Intelligence Brief to {RECIPIENT_EMAIL}...")
    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": FROM_EMAIL,
            "to": [RECIPIENT_EMAIL],
            "subject": f"The Preference Card — Sales Intelligence Brief {today.isoformat()}",
            "text": brief,
        },
        timeout=30,
    )
    if resp.status_code in (200, 201):
        print(f"  Sent! id={resp.json().get('id')}")
    else:
        print(f"  Email warning: {resp.status_code} {resp.text}")


def main():
    print(f"\n{'='*60}")
    print(f"The Preference Card — Sales Brief  |  {today.isoformat()}")
    print(f"{'='*60}\n")

    print("Fetching latest newsletter draft from Supabase...")
    draft = get_latest_draft()
    print(f"  Draft: {len(draft)} chars\n")

    brief = generate_sales_brief(draft)

    print("\n--- BRIEF PREVIEW ---")
    print(brief[:500])
    print("...\n")

    send_sales_brief(brief)

    print("\n✅ Sales Intelligence Brief complete!")


if __name__ == "__main__":
    main()
