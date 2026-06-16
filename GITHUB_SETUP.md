# GitHub Actions Setup — The Preference Card

This document covers the one-time setup needed to make the autonomous weekly newsletter run.

## What runs automatically

Every Friday at 6pm UTC (2pm ET / 11am PT), GitHub Actions:
1. Pulls 90 days of historical data from Supabase
2. Triggers the **Ortho Intel Newsletter Agent** on Claude Console
3. The agent web-searches for new FDA clearances, trials, PubMed, and industry news
4. Stores the draft in Supabase `newsletter_drafts`
5. Emails the draft to suresh.graf@gmail.com

---

## Step 1 — Push this folder to GitHub

```bash
cd "/Users/sureshgraf/Claude/Projects/Business Idea/the_preference_card"
git init           # (already done if .git exists)
git add .
git commit -m "Add GitHub Actions newsletter workflow"
```

Then create a new repo at https://github.com/new (name it `the-preference-card`) and push:
```bash
git remote add origin https://github.com/YOUR_USERNAME/the-preference-card.git
git push -u origin main
```

---

## Step 2 — Add GitHub Secrets

In your GitHub repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Add these 4 secrets:

| Secret name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic API key (from platform.claude.com → API Keys) |
| `SUPABASE_URL` | `https://eqahvsiukemnypkewhyu.supabase.co` |
| `SUPABASE_SERVICE_KEY` | Your Supabase service role key (from Supabase dashboard → Settings → API) |
| `RESEND_API_KEY` | Your Resend API key (see Step 3) |

---

## Step 3 — Set up Resend for email delivery

Resend sends the draft email to you every Friday.

1. Sign up free at https://resend.com
2. Add and verify your domain: `thepreferencecard.com`
   - Resend will give you DNS records to add in GoDaddy
3. Create an API key: Resend dashboard → API Keys → Create API Key
4. Add the key as `RESEND_API_KEY` in GitHub Secrets (Step 2)

> Free tier: 3,000 emails/month — more than enough.

---

## Step 4 — Test the workflow manually

Once the repo is pushed and secrets are set:

1. Go to your GitHub repo → **Actions** tab
2. Click **Weekly Newsletter Generation**
3. Click **Run workflow** → **Run workflow**
4. Watch the logs — it should complete in 5–15 minutes
5. Check your email and Supabase `newsletter_drafts` table

---

## Agent details

- **Agent ID**: `agent_01S3KbtaxU6GWCipdJqNcrcr`
- **Console**: https://platform.claude.com/workspaces/default/agents/agent_01S3KbtaxU6GWCipdJqNcrcr
- **Model**: claude-sonnet-4-6
- **Tools**: Built-in web search + web fetch

---

## Troubleshooting

**Session API fails** — The managed agents sessions API is in beta. If you get a 404 or auth error on `/v1/agents/{id}/sessions`, check the latest beta endpoint at https://docs.anthropic.com/en/docs/agents-and-tools/managed-agents

**Email not delivered** — Verify your domain in Resend and make sure the `FROM_EMAIL` in `run_newsletter.py` matches a verified domain.

**Supabase 401** — Make sure you're using the **service role** key (not the anon key) in the `SUPABASE_SERVICE_KEY` secret.
