# The Preference Card — Claude Code Project Brief

I'm building "The Preference Card" — a paid weekly orthopedic industry intelligence newsletter at thepreferencecard.com. I'm a former Arthrex sales rep (Minnesota territory, worked directly with surgeons in the OR). Here's what exists and what I need built:

## WHAT EXISTS

- `newsletter_agent.py` — fetches FDA 510(k) clearances, ClinicalTrials.gov registrations, PubMed articles, and RSS news feeds, then uses the Claude API to synthesize a formatted newsletter draft
- The agent runs and produces a draft, but FDA and RSS sections return 0 results and need debugging
- `ANTHROPIC_API_KEY` is set as an environment variable

## WHAT I NEED YOU TO BUILD

### 1. Fix FDA 510(k) data fetch
The API call succeeds (no error) but returns 0 orthopedic clearances. The correct working query (confirmed via web) is:
`https://api.fda.gov/device/510k.json?search=openfda.medical_specialty_description:"orthopedic"&sort=decision_date:desc&limit=50`
Date filtering should happen in Python after the fetch, not in the query string. The date format returned by the API is YYYY-MM-DD.

### 2. Fix RSS news feeds
The feed parser returns 0 items. Debug which feeds are accessible, fix date filtering logic, and add fallback sources if needed. Target sources: OrthoFeed, MDDI, MedTech Dive.

### 3. Add a SQLite database for historical accumulation
Every time the agent runs, store all fetched clearances, trials, articles, and news items in a local SQLite database (`preference_card.db`). This lets the agent build up a body of knowledge over time.

### 4. Add a historical context agent
When synthesizing the newsletter, query the database to surface related historical items. The synthesis prompt should include context like:
- "This is the 3rd arthroscopic cuff repair trial registered in 12 months"
- "Arthrex has received 5 clearances in the shoulder space this quarter vs. 2 for Stryker"
- "This device category saw a similar clearance in [date] — here's how the landscape has evolved"

This is the core intelligence layer that separates the product from a simple news aggregator.

### 5. Build a landing page
Simple one-page HTML site for thepreferencecard.com:
- Headline: "The Preference Card"
- Subheadline: "Weekly orthopedic intelligence for surgeons, reps, and medtech insiders"
- Brief description of what subscribers get
- Email signup form (integrate with Mailchimp or ConvertKit free tier)
- Clean, clinical aesthetic — dark navy or slate with white type, minimal
- Mobile responsive
- Save as `index.html` in a `/website` subfolder

## CONTEXT ON THE PRODUCT

The newsletter has 5 sections:
1. **FDA Watch** — new 510(k) clearances with field-level interpretation
2. **In the Pipeline** — new clinical trial registrations and what they signal
3. **From the Literature** — recent research with practical takeaways
4. **Market Moves** — funding, M&A, partnerships, launches
5. **The Field Perspective** — editor's commentary (added manually before sending)

The editor (me) adds the Field Perspective section manually each week — this is the human intelligence layer. Everything else is automated.

## TECH STACK
- Python 3
- Anthropic Python SDK (claude-opus-4-5 for synthesis)
- SQLite for persistence
- feedparser for RSS
- requests for API calls

Please read `newsletter_agent.py` first, then tackle the items above one at a time.
