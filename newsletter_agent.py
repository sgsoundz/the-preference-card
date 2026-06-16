#!/usr/bin/env python3
"""
The Preference Card — Orthopedic Intelligence Newsletter Agent
Fetches and synthesizes the week's most important orthopedic industry news
from the FDA, ClinicalTrials.gov, PubMed, and industry RSS feeds.

Usage:
    export ANTHROPIC_API_KEY="sk-ant-..."
    python3 newsletter_agent.py

Output:
    A markdown file saved to ~/The Preference Card/Drafts/
    ready for your review and field commentary before sending.
"""

import os
import json
import sqlite3
import smtplib
import requests
import feedparser
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from anthropic import Anthropic

# ─── Configuration ────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
LOOKBACK_DAYS = 7
OUTPUT_DIR = os.path.expanduser("~/The Preference Card/Drafts")
DB_PATH = os.path.expanduser("~/The Preference Card/preference_card.db")

# Email configuration — fill these in (see setup instructions)
EMAIL_SENDER = os.environ.get("PC_EMAIL_SENDER", "")      # your Gmail address
EMAIL_APP_PASSWORD = os.environ.get("PC_EMAIL_PASSWORD", "")  # Gmail App Password
EMAIL_RECIPIENT = "suresh.graf@gmail.com"

# ─── Database ─────────────────────────────────────────────────────────────────

def init_db():
    """Initialize SQLite database for historical accumulation."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS fda_clearances (
            k_number TEXT PRIMARY KEY,
            device TEXT,
            company TEXT,
            decision_date TEXT,
            decision TEXT,
            product_code TEXT,
            city TEXT,
            state TEXT,
            fetched_at TEXT
        );

        CREATE TABLE IF NOT EXISTS clinical_trials (
            nct_id TEXT PRIMARY KEY,
            title TEXT,
            sponsor TEXT,
            phase TEXT,
            status TEXT,
            summary TEXT,
            conditions TEXT,
            fetched_at TEXT
        );

        CREATE TABLE IF NOT EXISTS pubmed_articles (
            pmid TEXT PRIMARY KEY,
            title TEXT,
            authors TEXT,
            journal TEXT,
            pubdate TEXT,
            url TEXT,
            fetched_at TEXT
        );

        CREATE TABLE IF NOT EXISTS industry_news (
            url TEXT PRIMARY KEY,
            source TEXT,
            title TEXT,
            summary TEXT,
            pub_date TEXT,
            fetched_at TEXT
        );

        CREATE TABLE IF NOT EXISTS newsletters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            week_of TEXT,
            content TEXT,
            created_at TEXT
        );
    """)
    conn.commit()
    return conn


def save_clearances(conn, clearances):
    c = conn.cursor()
    for item in clearances:
        c.execute("""
            INSERT OR IGNORE INTO fda_clearances
            (k_number, device, company, decision_date, decision, product_code, city, state, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            item["k_number"], item["device"], item["company"],
            item["date"], item["decision"], item["product_code"],
            item["city"], item["state"], datetime.now().isoformat()
        ))
    conn.commit()


def save_trials(conn, trials):
    c = conn.cursor()
    for t in trials:
        c.execute("""
            INSERT OR IGNORE INTO clinical_trials
            (nct_id, title, sponsor, phase, status, summary, conditions, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            t["nct_id"], t["title"], t["sponsor"],
            json.dumps(t["phase"]), t["status"], t["summary"],
            json.dumps(t["conditions"]), datetime.now().isoformat()
        ))
    conn.commit()


def save_articles(conn, articles):
    c = conn.cursor()
    for a in articles:
        c.execute("""
            INSERT OR IGNORE INTO pubmed_articles
            (pmid, title, authors, journal, pubdate, url, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            a["pmid"], a["title"], json.dumps(a["authors"]),
            a["journal"], a["pubdate"], a["url"], datetime.now().isoformat()
        ))
    conn.commit()


def save_news(conn, news):
    c = conn.cursor()
    for n in news:
        c.execute("""
            INSERT OR IGNORE INTO industry_news
            (url, source, title, summary, pub_date, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            n["url"], n["source"], n["title"],
            n["summary"], n["date"], datetime.now().isoformat()
        ))
    conn.commit()


def save_newsletter(conn, content):
    c = conn.cursor()
    c.execute("""
        INSERT INTO newsletters (week_of, content, created_at)
        VALUES (?, ?, ?)
    """, (datetime.now().strftime("%Y-%m-%d"), content, datetime.now().isoformat()))
    conn.commit()


def get_historical_context(conn, lookback_weeks=12):
    """Pull relevant historical data for the synthesis agent to reference."""
    cutoff = (datetime.now() - timedelta(weeks=lookback_weeks)).isoformat()

    c = conn.cursor()

    # Companies that have appeared before
    c.execute("""
        SELECT company, COUNT(*) as count, GROUP_CONCAT(device, ' | ') as devices
        FROM fda_clearances
        WHERE fetched_at < date('now', '-7 days')
        GROUP BY company
        HAVING count > 1
        ORDER BY count DESC
        LIMIT 15
    """)
    repeat_companies = [dict(r) for r in c.fetchall()]

    # Recent trial activity by condition
    c.execute("""
        SELECT conditions, COUNT(*) as count
        FROM clinical_trials
        WHERE fetched_at >= ?
        GROUP BY conditions
        ORDER BY count DESC
        LIMIT 10
    """, (cutoff,))
    trial_trends = [dict(r) for r in c.fetchall()]

    # Total counts for context
    c.execute("SELECT COUNT(*) as n FROM fda_clearances WHERE fetched_at >= ?", (cutoff,))
    total_clearances = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) as n FROM clinical_trials WHERE fetched_at >= ?", (cutoff,))
    total_trials = c.fetchone()["n"]

    c.execute("SELECT COUNT(*) as n FROM pubmed_articles WHERE fetched_at >= ?", (cutoff,))
    total_articles = c.fetchone()["n"]

    # Recent newsletter count for streak awareness
    c.execute("SELECT COUNT(*) as n FROM newsletters")
    edition_count = c.fetchone()["n"]

    return {
        "editions_published": edition_count,
        "last_12_weeks": {
            "total_clearances": total_clearances,
            "total_trials": total_trials,
            "total_articles": total_articles,
        },
        "repeat_filers": repeat_companies[:10],
        "active_trial_areas": trial_trends[:8],
    }


# ─── Data Agents ──────────────────────────────────────────────────────────────

def fetch_fda_clearances(days_back=7):
    """Agent: Pulls recent orthopedic 510(k) clearances from the FDA API."""
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    url = "https://api.fda.gov/device/510k.json"
    params = {
        # advisory_committee_description is the correct searchable field for specialty
        "search": 'advisory_committee_description:"Orthopedic"',
        "sort": "decision_date:desc",
        "limit": 50,
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        clearances = []
        for item in r.json().get("results", []):
            date = item.get("decision_date", "")
            if date and date < cutoff:
                continue
            clearances.append({
                "device": item.get("device_name", ""),
                "company": item.get("applicant", ""),
                "date": date,
                "k_number": item.get("k_number", ""),
                "decision": item.get("decision_description", ""),
                "product_code": item.get("product_code", ""),
                "city": item.get("city", ""),
                "state": item.get("state", ""),
            })
        return clearances
    except Exception as e:
        print(f"  [FDA] Error: {e}")
        return []


def fetch_clinical_trials(days_back=7):
    """Agent: Pulls newly registered orthopedic clinical trials from ClinicalTrials.gov."""
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    url = "https://clinicaltrials.gov/api/v2/studies"
    params = {
        "query.cond": "orthopedic OR arthroplasty OR arthroscopy OR spine surgery OR bone implant",
        "filter.advanced": f"AREA[StudyFirstSubmitDate]RANGE[{cutoff}, MAX]",
        "fields": "NCTId,BriefTitle,LeadSponsorName,Phase,OverallStatus,BriefSummary,Condition",
        "pageSize": 20,
        "sort": "StudyFirstSubmitDate:desc",
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        trials = []
        for study in r.json().get("studies", []):
            proto = study.get("protocolSection", {})
            id_mod = proto.get("identificationModule", {})
            status_mod = proto.get("statusModule", {})
            desc_mod = proto.get("descriptionModule", {})
            sponsor_mod = proto.get("sponsorCollaboratorsModule", {})
            design_mod = proto.get("designModule", {})
            cond_mod = proto.get("conditionsModule", {})
            trials.append({
                "nct_id": id_mod.get("nctId", ""),
                "title": id_mod.get("briefTitle", ""),
                "sponsor": sponsor_mod.get("leadSponsor", {}).get("name", ""),
                "phase": design_mod.get("phases", []),
                "status": status_mod.get("overallStatus", ""),
                "summary": desc_mod.get("briefSummary", "")[:400],
                "conditions": cond_mod.get("conditions", []),
            })
        return trials
    except Exception as e:
        print(f"  [ClinicalTrials] Error: {e}")
        return []


def fetch_pubmed_articles(days_back=7):
    """Agent: Pulls recent orthopedic research abstracts from PubMed."""
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime("%Y/%m/%d")
    today = datetime.now().strftime("%Y/%m/%d")
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    search_params = {
        "db": "pubmed",
        "term": (
            f'(("orthopedic surgery"[MeSH] OR "arthroplasty"[MeSH] OR '
            f'"arthroscopy"[MeSH] OR "bone screws"[MeSH] OR "spinal fusion"[MeSH]) '
            f'AND ("{cutoff}"[PDAT]:"{today}"[PDAT]))'
        ),
        "retmax": 20,
        "sort": "date",
        "retmode": "json",
    }
    try:
        r = requests.get(f"{base}/esearch.fcgi", params=search_params, timeout=15)
        r.raise_for_status()
        pmids = r.json().get("esearchresult", {}).get("idlist", [])
        if not pmids:
            return []
        r2 = requests.get(
            f"{base}/esummary.fcgi",
            params={"db": "pubmed", "id": ",".join(pmids), "retmode": "json"},
            timeout=15,
        )
        r2.raise_for_status()
        data = r2.json().get("result", {})
        articles = []
        for pmid in pmids:
            if pmid in data:
                a = data[pmid]
                articles.append({
                    "pmid": pmid,
                    "title": a.get("title", ""),
                    "authors": [x.get("name", "") for x in a.get("authors", [])[:3]],
                    "journal": a.get("source", ""),
                    "pubdate": a.get("pubdate", ""),
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                })
        return articles
    except Exception as e:
        print(f"  [PubMed] Error: {e}")
        return []


def fetch_industry_news():
    """Agent: Pulls orthopedic industry news from RSS feeds.

    Uses requests to fetch feed content first, bypassing macOS SSL cert issues
    that cause feedparser to fail when called directly with a URL.
    """
    feeds = [
        ("OrthoFeed",              "https://orthofeed.com/feed/"),
        ("MDDI",                   "https://www.mddionline.com/rss/all"),
        ("MedTech Dive",           "https://www.medtechdive.com/feeds/news/"),
        ("Medical Device Network", "https://www.medicaldevice-network.com/feed/"),
    ]
    articles = []
    cutoff = datetime.now() - timedelta(days=14)

    headers = {"User-Agent": "Mozilla/5.0 (compatible; ThePreferenceCard/1.0)"}

    for source, url in feeds:
        try:
            resp = requests.get(url, timeout=15, headers=headers, verify=False)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)
            count = 0
            for entry in feed.entries[:15]:
                pub = entry.get("published_parsed")
                # Include entries with no date rather than silently dropping them
                if pub and datetime(*pub[:6]) < cutoff:
                    continue
                articles.append({
                    "source": source,
                    "title": entry.get("title", ""),
                    "summary": entry.get("summary", "")[:350],
                    "url": entry.get("link", ""),
                    "date": entry.get("published", ""),
                })
                count += 1
            print(f"     {source}: {count} items")
        except Exception as e:
            print(f"  [RSS:{source}] Error: {e}")

    return articles[:25]


# ─── Synthesis Agent ──────────────────────────────────────────────────────────

def synthesize_newsletter(clearances, trials, articles, news, historical, client):
    """Synthesis agent: uses Claude to turn raw data into a polished newsletter draft."""

    raw_data = json.dumps({
        "fda_clearances": clearances,
        "clinical_trials": trials[:12],
        "pubmed_articles": articles[:12],
        "industry_news": news[:20],
    }, indent=2)

    history_block = json.dumps(historical, indent=2)

    prompt = f"""You are the editorial engine for "The Preference Card," a weekly orthopedic industry intelligence newsletter.

ABOUT THE NEWSLETTER:
- Written by a former Arthrex sales rep who covered orthopedic surgery in Minnesota ORs
- Readers: surgeons, OR staff, device reps, hospital administrators, medtech investors
- Voice: credible, direct, field-native — the way a sharp rep would brief a colleague
- No fluff, no filler. Every sentence must earn its place.

HISTORICAL CONTEXT (use this to add pattern-level insight):
{history_block}

Use the historical context to surface trends when they're real and notable. Examples:
- "This is the 4th spine fusion trial registered this quarter — up from 1 in the same period last year."
- "Stryker has now filed 3 clearances in 60 days in this product category."
- "This edition is #{historical.get('editions_published', 0) + 1} of The Preference Card."
Only cite the pattern if it's genuinely interesting. Don't force it.

YOUR JOB:
Write a complete newsletter draft from the raw data below. Use only what's in the data — do not invent or speculate. For each item, include the real names, companies, and dates from the data.

FORMAT (follow exactly):

---
# THE PREFERENCE CARD
**Orthopedic Industry Intelligence** | Week of {datetime.now().strftime("%B %d, %Y")}

---

## 🔬 FDA WATCH
*New clearances and regulatory moves*

[3–5 bullet points. Format: **Device Name (Company, K-number)** — one sentence on clinical or competitive significance. Skip routine commodity clearances unless volume is notable. Lead with the most consequential.]

---

## 🧪 IN THE PIPELINE
*New clinical trials worth watching*

[2–4 bullet points. Format: **Trial title (Sponsor, NCT#, Phase)** — one sentence on what this trial could mean for surgical practice or market share when results land.]

---

## 📄 FROM THE LITERATURE
*Research with real-world implications*

[3–4 bullet points. Format: **"Study title" — Journal, Authors** — one sentence on the finding and its practical takeaway for surgeons or device companies. Skip basic science with no clinical hook.]

---

## 📰 MARKET MOVES
*Deals, funding, and signals*

[3–5 bullet points from the news feed. Cover funding rounds, acquisitions, partnerships, leadership changes, or launches that signal where investment and attention is flowing.]

---

## 🏥 THE FIELD PERSPECTIVE
*Editor's note — [TO BE ADDED BEFORE SENDING]*

> This section will contain 2–3 sentences of field-level commentary from the editor — connecting this week's signals to what's actually happening in ORs and hospital purchasing decisions. Leave this section as a placeholder.

---

*The Preference Card publishes weekly. Forward-worthy? Share it. Subscribe at [thepreferencecard.com](https://thepreferencecard.com)*

---

RAW DATA:
{raw_data}

Write the newsletter now. Real names, real numbers, specific — not generic."""

    message = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


# ─── Email Delivery ───────────────────────────────────────────────────────────

def send_email_draft(newsletter_text, filepath, edition_number):
    """Send the newsletter draft to suresh.graf@gmail.com via Gmail SMTP."""
    if not EMAIL_SENDER or not EMAIL_APP_PASSWORD:
        print("   ⚠ Email not configured — skipping. Set PC_EMAIL_SENDER and PC_EMAIL_PASSWORD.")
        return False

    subject = f"📋 Preference Card Draft — Edition #{edition_number} ({datetime.now().strftime('%B %d, %Y')})"

    # Plain-text body wraps the markdown draft
    body = f"""Your weekly newsletter draft is ready for review.

Add your Field Perspective, then upload to Beehiiv.

Draft saved locally at:
{filepath}

────────────────────────────────────────
{newsletter_text}
────────────────────────────────────────

— The Preference Card Agent
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECIPIENT
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_APP_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECIPIENT, msg.as_string())
        print(f"   ✓ Draft emailed to {EMAIL_RECIPIENT}")
        return True
    except Exception as e:
        print(f"   ✗ Email failed: {e}")
        return False


# ─── Orchestrator ─────────────────────────────────────────────────────────────

def main():
    print()
    print("╔══════════════════════════════════════════╗")
    print("║    THE PREFERENCE CARD — Newsletter Agent  ║")
    print("╚══════════════════════════════════════════╝")
    print()

    if not ANTHROPIC_API_KEY:
        print("ERROR: ANTHROPIC_API_KEY environment variable not set.")
        print("       Run: export ANTHROPIC_API_KEY='sk-ant-...'")
        return

    client = Anthropic(api_key=ANTHROPIC_API_KEY)

    print("Initializing database...")
    conn = init_db()
    print(f"   ✓ Database ready at {DB_PATH}\n")

    print(f"Looking back {LOOKBACK_DAYS} days ({(datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime('%b %d')} → today)\n")

    print("① Fetching FDA 510(k) clearances...")
    clearances = fetch_fda_clearances(LOOKBACK_DAYS)
    print(f"   ✓ {len(clearances)} orthopedic clearances found")
    if clearances:
        save_clearances(conn, clearances)
        print("   ✓ Saved to database\n")
    else:
        print()

    print("② Fetching ClinicalTrials.gov registrations...")
    trials = fetch_clinical_trials(LOOKBACK_DAYS)
    print(f"   ✓ {len(trials)} new trials found")
    if trials:
        save_trials(conn, trials)
        print("   ✓ Saved to database\n")
    else:
        print()

    print("③ Fetching PubMed research articles...")
    articles = fetch_pubmed_articles(LOOKBACK_DAYS)
    print(f"   ✓ {len(articles)} articles found")
    if articles:
        save_articles(conn, articles)
        print("   ✓ Saved to database\n")
    else:
        print()

    print("④ Fetching industry news (RSS feeds)...")
    news = fetch_industry_news()
    print(f"   ✓ {len(news)} news items found")
    if news:
        save_news(conn, news)
        print("   ✓ Saved to database\n")
    else:
        print()

    print("⑤ Loading historical context from database...")
    historical = get_historical_context(conn)
    print(f"   ✓ {historical['editions_published']} prior editions | "
          f"{historical['last_12_weeks']['total_clearances']} clearances in 12 weeks\n")

    print("⑥ Synthesizing newsletter with Claude...")
    newsletter = synthesize_newsletter(clearances, trials, articles, news, historical, client)
    print("   ✓ Draft complete\n")

    save_newsletter(conn, newsletter)
    conn.close()

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filename = f"preference_card_{datetime.now().strftime('%Y_%m_%d')}.md"
    filepath = os.path.join(OUTPUT_DIR, filename)
    with open(filepath, "w") as f:
        f.write(newsletter)

    print(f"✅ Newsletter saved to:\n   {filepath}\n")

    print("⑦ Emailing draft to suresh.graf@gmail.com...")
    send_email_draft(newsletter, filepath, historical["editions_published"] + 1)

    print()
    print("─" * 50)
    print("NEXT STEPS:")
    print("  1. Check your email for the draft")
    print("  2. Add your Field Perspective commentary")
    print("  3. Upload to Beehiiv and send")
    print("─" * 50)
    print()
    print("── PREVIEW ──")
    print(newsletter[:800])
    print("...")


if __name__ == "__main__":
    main()
