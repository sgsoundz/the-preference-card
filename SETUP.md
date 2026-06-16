# The Preference Card — Setup Guide

## What this does
Runs every week and produces a full newsletter draft by pulling live data from:
- **FDA** — new orthopedic 510(k) clearances
- **ClinicalTrials.gov** — newly registered orthopedic studies
- **PubMed** — recent orthopedic research publications
- **RSS feeds** — OrthoFeed, MDDI, MedTech Dive, Medical Device Network

Claude synthesizes everything into a formatted draft. You add your **Field Perspective** section, then publish to Beehiiv.

---

## One-time setup

### 1. Install Python dependencies
```bash
pip3 install anthropic requests feedparser
```

### 2. Get your Anthropic API key
Go to [console.anthropic.com](https://console.anthropic.com) → API Keys → Create Key

Add it to your shell (paste this in Terminal, replace with your key):
```bash
echo 'export ANTHROPIC_API_KEY="sk-ant-YOUR-KEY-HERE"' >> ~/.zshrc
source ~/.zshrc
```

### 3. Run it
```bash
cd ~/path/to/the_preference_card
python3 newsletter_agent.py
```

Your draft will be saved to `~/The Preference Card/Drafts/`.

---

## Weekly workflow

1. **Run the agent** (Monday morning is a good cadence)
2. **Open the draft** — it's a `.md` file, readable in any text editor
3. **Add your Field Perspective** — 2–3 sentences connecting the week's signals to what you know is actually happening in ORs and hospital purchasing
4. **Upload to Beehiiv** — paste the content, preview, send

---

## Setting up automated weekly runs (optional)

Run every Monday at 7am:
```bash
crontab -e
```
Add this line:
```
0 7 * * 1 cd /path/to/the_preference_card && python3 newsletter_agent.py
```

---

## Beehiiv setup
1. Create account at [beehiiv.com](https://beehiiv.com)
2. Connect your domain: `thepreferencecard.com`
3. Set up paid tier ($299–499/year) once you have your first 50 subscribers
4. Use your Microsoft 365 email (`@thepreferencecard.com`) for sender address

---

## Cost to run
- Anthropic API: ~$0.05–0.15 per newsletter issue (Claude Opus)
- Well under $1/week at any reasonable subscriber count
