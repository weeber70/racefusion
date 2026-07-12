# RaceFusion Product Roadmap

## Current Status
- Live beta on Streamlit Community Cloud
- Single-file Python/Streamlit app (app.py, ~5,500+ lines)
- Supabase PostgreSQL backend
- All core features built and functional
- Stripe account: ready
- Apple Developer enrollment: pending approval

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

**Pricing options to decide before launch:**
- Monthly only vs. monthly + annual (annual = 2 months free is a common conversion tool)

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
  - Apple Developer enrollment pending (emailed Apple support)
  - 500k free API calls/month
  - Supports historical weather back to Aug 2021
  - Replaces current NOAA METAR API
  - **After WeatherKit is live:** build "Recalculate Weather" button in Admin Panel
    - Loops through all existing runs
    - Fetches correct historical weather from WeatherKit for each run's date/location
    - Recalculates DA using accurate data
    - Updates Supabase records in one shot
    - Note: build the recalculate logic as a clean reusable function first — WeatherKit just plugs into it when approved

### Phase 2 — Paywall (target: Sep–Oct 2026)
- Stripe integration (account already set up)
- 30-day free trial logic (all features unlocked)
- Subscription tier enforcement in Supabase
- Feature gating:
  - RacePak section hidden for Racer tier
  - Trial expiry/upgrade prompt UI
- Technical needs:
  - `subscription_tier` field in user record
  - `trial_start_date` field
  - Stripe webhook to update tier on payment/cancellation
- Move hosting from Streamlit Community Cloud to Railway or Render (one-time ~2 hour task)

### Phase 2.5 — Referral Program
- Each user gets a unique referral code
- Referral mechanic: "Refer a friend who subscribes — you both get one free month"
- Built on Stripe's coupon/credit infrastructure
- Technical needs:
  - `referral_code` field per user in Supabase
  - Track successful referral conversions
  - Auto-apply free month credit via Stripe when referral subscribes
- Why it works for drag racing: tight-knit community, racers trust other racers, live demos happen naturally at the track

### Phase 3 — Multiple Cars + Teams (target: Q4 2026)
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

### Phase 4 — Scaling (when user growth requires it)
See Scaling section below.

### Future Consideration
- Mobile-responsive web layout
- React Native mobile app (timeslip upload + ET predictor)
- Stripe per-seat pricing option for larger teams
- Physical weather station integration (Kestrel, Ambient Weather Network)
- YouTube video link field on runs
- UptimeRobot monitoring (verify still active)

---

## Scaling Plan

### Market Context
- ~40,000 drag racers in the US
- Target: 10% = ~4,000 users
- At that scale, Streamlit Community Cloud hosting needs to move to paid hosting (not a Streamlit limitation — a hosting limitation)

### Revenue Projection (at 4,000 users)
| Tier | Est. Users | Monthly Revenue |
|---|---|---|
| Racer $9.99 | 1,000 | $9,990 |
| Pro $19.99 | 2,000 | $39,980 |
| Elite $34.99 | 1,000 | $34,990 |
| **Total** | **4,000** | **~$85,000/mo** |

### Infrastructure Changes Needed

**Hosting (move off Streamlit Community Cloud):**
- Already needed at Phase 2 launch — move to Railway, Render, or DigitalOcean
- Each active Streamlit session uses ~250MB RAM
- 1,000 concurrent users needs ~16GB RAM server (~$100–300/mo)
- Realistic concurrent usage at 4,000 total users: 1–3% = 40–120 simultaneous

**Concurrent user capacity by server size:**
| Server RAM | Concurrent Users | Approx Monthly Cost |
|---|---|---|
| 4GB | ~15 users | $24–40/mo |
| 8GB | ~30 users | $48–80/mo |
| 16GB | ~60 users | $96–160/mo |
| 32GB | ~120 users | $200–300/mo |

**Database (Supabase):**
- Free tier: fine for beta
- Supabase Pro ($25/mo): upgrade at ~500 active users
- Supabase scales well through this entire growth phase

**Frontend (long term only):**
- Streamlit is viable well into the scaling phase
- React Native / Next.js rebuild only necessary if mobile app and web need shared codebase
- Not needed before 2027

### Scaling Trigger Points
- 100 users → monitor Streamlit Community Cloud performance
- 500 users → upgrade Supabase to Pro, migrate to paid hosting
- 1,000 users → evaluate larger server or multiple instances
- 4,000 users → evaluate full infrastructure review

---

## Realistic Timeline
- **Aug 2026** → Apple WeatherKit live
- **Sep–Oct 2026** → Paywall + Stripe live, start charging, move to paid hosting
- **Q4 2026** → Multiple cars + team logins + referral program
- **Q1 2027** → 1,000 users target

---

## Security Constraints (permanent)
- Do NOT commit: .env file, runs/ folder, users/ folder
- API keys must NEVER be stored in racefusion_config.json or any file committed to GitHub
- API keys must never be shared in chats
