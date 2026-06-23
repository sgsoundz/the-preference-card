"""
run_newsletter.py — Weekly Ortho Intel Newsletter Runner
Called by GitHub Actions every Friday at 6pm UTC.

Flow:
  1. Fetch fresh data from FDA 510(k), ClinicalTrials.gov, PubMed, RSS (past 7 days)
  2. Store new items in Supabase (deduped by primary key)
  3. Pull last 90 days from Supabase for longitudinal context
  4. Call Claude API to synthesize 5-section newsletter draft
  5. Store draft in Supabase newsletter_drafts
  6. Email draft to suresh.graf@gmail.com via Resend
"""

import os
import sys
import json
import time
import hashlib
import datetime
import xml.etree.ElementTree as ET

import httpx
import anthropic

# ── Config ─────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
SUPABASE_URL      = os.environ["SUPABASE_URL"]
SUPABASE_KEY      = os.environ["SUPABASE_SERVICE_KEY"]
RESEND_API_KEY    = os.environ.get("RESEND_API_KEY", "")
RECIPIENT_EMAIL   = os.environ.get("RECIPIENT_EMAIL", "suresh.graf@gmail.com")
FROM_EMAIL        = "newsletter@thepreferencecard.com"

today           = datetime.date.today()
seven_days_ago  = today - datetime.timedelta(days=7)
ninety_days_ago = today - datetime.timedelta(days=90)
issue_date      = today.isoformat()

SYSTEM_PROMPT = """You are a weekly orthopedic medical device intelligence analyst writing for \
The Preference Card newsletter. Your audience is orthopedic surgery professionals and medical \
device industry insiders.

Synthesize the data provided into a polished 5-section newsletter in formatted markdown:

1. **FDA Watch** — New 510(k) clearances. Device name, applicant, product code, date. \
Flag if a company has multiple clearances this quarter (trend signal).

2. **In the Pipeline** — Notable new and updated clinical trials. NCT ID, sponsor, indication, \
phase. Flag if an indication is seeing a surge in new trials.

3. **From the Literature** — Key PubMed findings. Lead author, journal, key takeaway in 1-2 sentences.

4. **Market Moves** — Industry news: M&A, funding, partnerships, launches, leadership changes.

5. **✏️ The Field Perspective** — Leave this section as a clearly marked placeholder: \
"[EDITOR: Add your Field Perspective here — what did you see in the OR this week?]"

Be precise with dates, device names, companies, and trial identifiers. \
Flag longitudinal trends prominently using bold text. \
If a section has no new items, say "Nothing notable this week." \
End with a horizontal rule and: "Draft generated {date}. Review before sending."
"""


# ── Supabase helpers ────────────────────────────────────────────────────────────
def sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }

def sb_get(table: str, params: dict = None) -> list:
    resp = httpx.get(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers=sb_headers(), params=params, timeout=30
    )
    resp.raise_for_status()
    return resp.json()

def sb_upsert(table: str, rows: list, on_conflict: str = "id") -> None:
    if not rows:
        return
    resp = httpx.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={**sb_headers(), "Prefer": f"resolution=ignore-duplicates"},
        json=rows, timeout=30
    )
    resp.raise_for_status()

def sb_insert(table: str, data: dict) -> list:
    resp = httpx.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={**sb_headers(), "Prefer": "return=representation"},
        json=data, timeout=30
    )
    resp.raise_for_status()
    return resp.json()


# ── Data fetchers ───────────────────────────────────────────────────────────────
def fetch_fda() -> list:
    """Fetch recent 510(k) orthopedic clearances from openFDA."""
    print("Fetching FDA 510(k) clearances...")
    url = "https://api.fda.gov/device/510k.json"
    # advisory_committee_description is reliable; spaces around TO fix %2B encoding bug
    params = {
        "search": f'advisory_committee:"OR" AND decision_date:[{seven_days_ago.strftime("%Y%m%d")} TO 99991231]',
        "limit": "50",
        "sort": "decision_date:desc",
    }
    print(f"  FDA query: {params['search']}")
    try:
        resp = httpx.get(url, params=params, timeout=30)
        print(f"  FDA response: HTTP {resp.status_code}")
        if resp.status_code != 200:
            print(f"  FDA error body: {resp.text[:300]}")
        resp.raise_for_status()
        results = resp.json().get("results", [])
        rows = []
        for r in results:
            rows.append({
                "id": r.get("k_number", ""),
                "device_name": r.get("device_name", ""),
                "applicant": r.get("applicant", ""),
                "decision_date": r.get("decision_date", "")[:10] if r.get("decision_date") else None,
                "product_code": r.get("product_code", ""),
                "advisory_committee": r.get("advisory_committee", ""),
                "statement_or_summary": r.get("statement_or_summary", ""),
            })
        print(f"  FDA: {len(rows)} new clearances")
        return rows
    except Exception as e:
        print(f"  FDA fetch error: {e}")
        return []


def fetch_trials() -> list:
    """Fetch new orthopedic clinical trials from ClinicalTrials.gov."""
    print("Fetching clinical trials...")
    url = "https://clinicaltrials.gov/api/v2/studies"
    params = {
        "query.cond": "orthopedic OR arthroplasty OR spine OR fracture OR sports medicine",
        "filter.advanced": f"AREA[StartDate]RANGE[{seven_days_ago.isoformat()},MAX]",
        "fields": "NCTId,BriefTitle,OverallStatus,Phase,LeadSponsorName,Condition,InterventionName,StartDate,StudyFirstPostDate",
        "pageSize": "50",
        "sort": "StartDate:desc",
    }
    try:
        resp = httpx.get(url, params=params, timeout=30)
        resp.raise_for_status()
        studies = resp.json().get("studies", [])
        rows = []
        for s in studies:
            p = s.get("protocolSection", {})
            id_mod = p.get("identificationModule", {})
            stat_mod = p.get("statusModule", {})
            design_mod = p.get("designModule", {})
            sponsor_mod = p.get("sponsorCollaboratorsModule", {})
            cond_mod = p.get("conditionsModule", {})
            interv_mod = p.get("armsInterventionsModule", {})
            nct_id = id_mod.get("nctId", "")
            if not nct_id:
                continue
            rows.append({
                "nct_id": nct_id,
                "title": id_mod.get("briefTitle", ""),
                "status": stat_mod.get("overallStatus", ""),
                "phase": (design_mod.get("phases", [None]) or [None])[0],
                "sponsor": sponsor_mod.get("leadSponsor", {}).get("name", ""),
                "conditions": cond_mod.get("conditions", []),
                "interventions": [i.get("name","") for i in interv_mod.get("interventions", [])],
                "start_date": stat_mod.get("startDateStruct", {}).get("date"),
                "registration_date": stat_mod.get("studyFirstPostDateStruct", {}).get("date"),
                "url": f"https://clinicaltrials.gov/study/{nct_id}",
            })
        print(f"  Trials: {len(rows)} new registrations")
        return rows
    except Exception as e:
        print(f"  Trials fetch error: {e}")
        return []


def fetch_pubmed() -> list:
    """Fetch recent orthopedic PubMed articles."""
    print("Fetching PubMed articles...")
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    query = (
        "(orthopedic[tiab] OR arthroplasty[tiab] OR spine surgery[tiab] OR "
        "fracture fixation[tiab] OR sports medicine[tiab]) AND "
        f"(\"{seven_days_ago.strftime('%Y/%m/%d')}\"[PDAT] : \"3000\"[PDAT])"
    )
    try:
        search = httpx.get(f"{base}/esearch.fcgi", params={
            "db": "pubmed", "term": query, "retmax": "30",
            "sort": "date", "retmode": "json",
        }, timeout=30)
        search.raise_for_status()
        ids = search.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            print("  PubMed: 0 articles")
            return []
        fetch = httpx.get(f"{base}/efetch.fcgi", params={
            "db": "pubmed", "id": ",".join(ids),
            "rettype": "xml", "retmode": "xml",
        }, timeout=30)
        fetch.raise_for_status()
        root = ET.fromstring(fetch.text)
        rows = []
        for art in root.findall(".//PubmedArticle"):
            pmid = art.findtext(".//PMID", "")
            title = art.findtext(".//ArticleTitle", "")
            journal = art.findtext(".//Journal/Title", "")
            abstract = " ".join(t.text or "" for t in art.findall(".//AbstractText"))
            pub_year = art.findtext(".//PubDate/Year", "")
            pub_month = art.findtext(".//PubDate/Month", "01")
            pub_day = art.findtext(".//PubDate/Day", "01")
            try:
                pub_date = datetime.datetime.strptime(
                    f"{pub_year}-{pub_month[:3]}-{pub_day}", "%Y-%b-%d"
                ).date().isoformat()
            except Exception:
                pub_date = f"{pub_year}-01-01" if pub_year else None
            authors = [
                f"{a.findtext('LastName','')} {a.findtext('Initials','')}".strip()
                for a in art.findall(".//Author")[:5]
            ]
            rows.append({
                "pmid": pmid,
                "title": title,
                "authors": authors,
                "journal": journal,
                "pub_date": pub_date,
                "abstract": abstract[:1000],
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            })
        print(f"  PubMed: {len(rows)} articles")
        return rows
    except Exception as e:
        print(f"  PubMed fetch error: {e}")
        return []


def fetch_news() -> list:
    """Fetch orthopedic industry news from RSS feeds."""
    print("Fetching industry news...")
    feeds = [
        ("OrthoFeed",    "https://www.orthofeed.com/feed/"),
        ("MDDI Online",  "https://www.mddionline.com/rss.xml"),
        ("MedTech Dive", "https://www.medtechdive.com/feeds/news/"),
        ("DeviceTalks",  "https://www.devicetalks.com/feed/"),
    ]
    rows = []
    cutoff = seven_days_ago.isoformat()
    for source, url in feeds:
        try:
            resp = httpx.get(url, timeout=20, follow_redirects=True)
            if resp.status_code != 200:
                continue
            root = ET.fromstring(resp.text)
            for item in root.findall(".//item")[:30]:
                title = item.findtext("title", "").strip()
                link  = item.findtext("link", "").strip()
                desc  = item.findtext("description", "").strip()
                pub_raw = item.findtext("pubDate", "")
                try:
                    pub_dt = datetime.datetime(*time.strptime(pub_raw, "%a, %d %b %Y %H:%M:%S %z")[:6])
                    pub_date = pub_dt.date().isoformat()
                except Exception:
                    pub_date = today.isoformat()
                if pub_date < cutoff:
                    continue
                ortho_kw = ["ortho","spine","joint","hip","knee","shoulder","implant",
                            "device","surgical","arthro","fracture","medtech"]
                combined = (title + desc).lower()
                if not any(k in combined for k in ortho_kw):
                    continue
                item_id = hashlib.md5(link.encode()).hexdigest()
                rows.append({
                    "id": item_id,
                    "title": title,
                    "source": source,
                    "url": link,
                    "summary": desc[:500],
                    "pub_date": pub_date,
                })
        except Exception as e:
            print(f"  {source} feed error: {e}")
    print(f"  News: {len(rows)} items")
    return rows


# ── Build context ───────────────────────────────────────────────────────────────
def build_context(fda, trials, pubmed, news) -> str:
    """Build the user message combining fresh data + historical counts."""
    # Historical counts for trend context
    hist_fda    = sb_get("fda_clearances",  {"decision_date": f"gte.{ninety_days_ago}", "select": "id,device_name,applicant,decision_date,product_code"})
    hist_trials = sb_get("clinical_trials", {"start_date": f"gte.{ninety_days_ago}", "select": "nct_id,title,sponsor,status,start_date"})

    lines = [
        f"Today: {issue_date}. Generate this week's issue of **The Preference Card**.",
        "",
        "## NEW THIS WEEK (past 7 days) — synthesize these into the newsletter",
        "",
        f"### FDA 510(k) Clearances — {len(fda)} new",
    ]
    for r in fda:
        lines.append(f"- {r.get('decision_date','?')} | **{r.get('device_name','')}** | {r.get('applicant','')} | Code: {r.get('product_code','')}")

    lines += ["", f"### Clinical Trials — {len(trials)} new registrations"]
    for r in trials:
        lines.append(f"- {r.get('start_date','?')} | {r.get('nct_id','')} | {r.get('title','')[:90]} | {r.get('sponsor','')}")

    lines += ["", f"### PubMed Articles — {len(pubmed)} new"]
    for r in pubmed:
        auths = ", ".join((r.get("authors") or [])[:3])
        lines.append(f"- {r.get('pub_date','?')} | {r.get('title','')[:90]} | {auths} | {r.get('journal','')}")

    lines += ["", f"### Industry News — {len(news)} items"]
    for r in news:
        lines.append(f"- {r.get('pub_date','?')} | **{r.get('title','')[:80]}** | {r.get('source','')}")

    lines += [
        "",
        "## LONGITUDINAL CONTEXT (last 90 days in database)",
        f"- FDA clearances on file: {len(hist_fda)}",
    ]
    # Company clearance counts
    from collections import Counter
    company_counts = Counter(r.get("applicant","") for r in hist_fda if r.get("applicant"))
    top = company_counts.most_common(5)
    if top:
        lines.append("  Top applicants 90d: " + ", ".join(f"{c} ({n})" for c,n in top))

    lines.append(f"- Clinical trials on file: {len(hist_trials)}")
    sponsor_counts = Counter(r.get("sponsor","") for r in hist_trials if r.get("sponsor"))
    top_s = sponsor_counts.most_common(5)
    if top_s:
        lines.append("  Top sponsors 90d: " + ", ".join(f"{s} ({n})" for s,n in top_s))

    return "\n".join(lines)


# ── Claude synthesis ────────────────────────────────────────────────────────────
def synthesize(user_message: str) -> str:
    """Call Claude API to synthesize the newsletter draft."""
    print("Calling Claude API to synthesize newsletter...")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    text = ""
    for block in response.content:
        if hasattr(block, "text"):
            text += block.text
    print(f"  Draft: {len(text)} chars")
    return text


# ── Email via Resend ────────────────────────────────────────────────────────────
def send_email(draft: str):
    if not RESEND_API_KEY:
        print("RESEND_API_KEY not set — skipping email.")
        return
    print(f"Emailing draft to {RECIPIENT_EMAIL}...")
    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        json={
            "from": FROM_EMAIL,
            "to": [RECIPIENT_EMAIL],
            "subject": f"The Preference Card — Draft {issue_date}",
            "text": draft,
        },
        timeout=30,
    )
    if resp.status_code in (200, 201):
        print(f"  Sent! id={resp.json().get('id')}")
    else:
        print(f"  Email warning: {resp.status_code} {resp.text}")


# ── Main ────────────────────────────────────────────────────────────────────────
def main():
    print(f"\n{'='*60}")
    print(f"The Preference Card — Weekly Run  |  {issue_date}")
    print(f"{'='*60}\n")

    # 1. Fetch fresh data
    fda    = fetch_fda()
    trials = fetch_trials()
    pubmed = fetch_pubmed()
    news   = fetch_news()

    # 2. Store new items in Supabase (ignore duplicates)
    print("\nStoring new items in Supabase...")
    if fda:    sb_upsert("fda_clearances",  fda,    on_conflict="id")
    if trials: sb_upsert("clinical_trials", trials, on_conflict="nct_id")
    if pubmed: sb_upsert("pubmed_articles", pubmed, on_conflict="pmid")
    if news:   sb_upsert("news_items",      news,   on_conflict="id")
    print("  Done.")

    # 3. Build context message
    user_message = build_context(fda, trials, pubmed, news)
    print(f"\nContext message: {len(user_message)} chars")

    # 4. Synthesize with Claude
    draft = synthesize(user_message)

    print("\n--- DRAFT PREVIEW ---")
    print(draft[:600])
    print("...\n")

    # 5. Save draft to Supabase
    print("Saving draft to Supabase...")
    sb_insert("newsletter_drafts", {
        "issue_date":    issue_date,
        "draft_markdown": draft,
        "fda_count":     len(fda),
        "trials_count":  len(trials),
        "pubmed_count":  len(pubmed),
        "news_count":    len(news),
    })

    # 6. Email
    send_email(draft)

    print("\n✅ Newsletter run complete!")


if __name__ == "__main__":
    main()
