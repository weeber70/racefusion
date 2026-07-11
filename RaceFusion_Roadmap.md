# RaceFusion Product Roadmap

## Current Status
- Live beta on Streamlit Community Cloud
- Single-file Python/Streamlit app (app.py, ~5,500+ lines)
- Supabase PostgreSQL backend
- All core features built and functional

---

## Subscription Tiers

| Feature | Racer $9.99/mo | Pro $19.99/mo | Elite $34.99/mo |
|---|---|---|---|
| Free Trial | 30 days (all features) | 30 days (all features) | 30 days (all features) |
| Timeslips | Unlimited | Unlimited | Unlimited |
| ET Predictor | Yes | Yes | Yes |
| Weather / DA | Yes | Yes | Yes |
| RacePak Charts | No | Yes | Yes |
| Cars | 1 | 1 | Unlimited |
| Team Logins | No | No | 3 (+ owner) |

> Tier names TBD — options considered: Bracket / Full Pass / Garage, Timeslip / Analyzer / Stable, Starter / Data / Team

---

## Elite Team Login Details
- Owner account + 3 team member logins
- Owner controls permissions per team member:
  - View runs
  - Add runs
  - Delete runs
  - View RacePak charts
  - View ET Predictor
  - Which cars they can access
- Team members self-manage their own credentials
- Invite flow:
  1. Owner enters team member email in account settings
  2. RaceFusion sends invite link (expires 48 hours)
  3. Team member clicks link, creates their own username/password
  4. Account automatically linked to owner's Elite account
  5. Owner assigns permissions

---

## Phase Roadmap

### Now — Immediate
- Apple WeatherKit integration
  - Apple Developer enrollment pending approval ($99/year)
  - 500k free API calls/month
  - Supports historical weather back to Aug 2021
  - Replaces current NOAA METAR API

### Phase 2 — Paywall
- Stripe integration
- 30-day free trial logic (all features unlocked)
- Subscription tier enforcement in Supabase
- Feature gating:
  - RacePak section hidden for Racer tier
  - Trial expiry/upgrade prompt UI
- Technical needs:
  - `subscription_tier` field in user record
  - `trial_start_date` field
  - Stripe webhook to update tier on payment/cancellation

### Phase 3 — Multiple Cars + Teams
- Multiple cars (Elite tier)
  - New `cars` table in Supabase
  - Car switcher in sidebar
  - Runs linked to specific car
  - ET Predictor filters by selected car
  - Refactor triggers natural file split (cars.py, db.py, etc.)
- Team logins (Elite tier)
  - `invites` table: token, owner account, email, expiry
  - `team_members` table: links team user to owner account + permissions
  - Invite email via SendGrid or Supabase Auth
  - Special invite landing page in app
  - Per-member permission management UI for owner

### Future Consideration
- Mobile-responsive web layout (Phase 1.5)
- React Native mobile app (timeslip upload + ET predictor)
- Stripe per-seat pricing option for larger teams
- Physical weather station integration (Kestrel, Ambient Weather Network)
- YouTube video link field on runs
- UptimeRobot monitoring (verify still active)

---

## Security Constraints (permanent)
- Do NOT commit: .env file, runs/ folder, users/ folder
- API keys must NEVER be stored in racefusion_config.json or any file committed to GitHub
- API keys must never be shared in chats
