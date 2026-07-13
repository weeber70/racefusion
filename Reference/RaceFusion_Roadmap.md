# RaceFusion Product Roadmap

## Current Status
- Live beta on Streamlit Community Cloud
- Single-file Python/Streamlit app (app.py, ~5,500+ lines)
- Supabase PostgreSQL backend
- All core features built and functional
- Stripe account: ready, Sandbox (test) mode active
- Stripe products created in Sandbox: Racer ($9.99), Pro ($19.99), Crew Chief ($34.99)
- Apple Developer enrollment: pending approval (emailed Apple support)
- Domain: racefusion.com already pointing to app

---

## Subscription Tiers

| Feature | Racer $9.99/mo | Pro $19.99/mo | Crew Chief $34.99/mo |
|---|---|---|---|
| Free Trial | 30 days (all features) | 30 days (all features) | 30 days (all features) |
| Timeslips | Unlimited | Unlimited | Unlimited |
| ET Predictor | Yes | Yes | Yes |
| Weather / DA | Yes | Yes | Yes |
| Channel Charts (DAQ) | No | Yes | Yes |
| Cars | 1 | 1 | Unlimited (coming soon) |
| Team Logins | No | No | 3 + owner (coming soon) |

> Tier names final: Racer / Pro / Crew Chief

**Stripe descriptions:**
- Racer: "Timeslip tracking, ET Predictor, and weather/DA analysis"
- Pro: "Everything in Racer plus full DAQ data analysis and channel charts"
- Crew Chief: "Everything in Pro plus unlimited cars and team logins (coming soon)"

**Pricing:** Monthly only to start. Annual option (2 months free) to be added later.

**Crew Chief interim strategy:**
- Multiple cars and team logins not yet built
- Crew Chief users get everything Pro gets in the interim
- Display "Multiple cars and team logins — coming soon" in UI
- Notify Crew Chief users when features go live

---

## Crew Chief Team Login Details (Phase 3)
- Owner account + 3 team member logins
- Owner controls permissions per team member:
  - View runs
  - Add runs
  - Delete runs
  - View Channel Charts
  - View ET Predictor
  - Which cars they can access
- Team members self-manage their own credentials
- Invite flow:
  1. Owner enters team member email in account settings
  2. RaceFusion sends invite link (expires 48 hours)
  3. Team member clicks link, creates their own username/password
  4. Account automatically linked to owner's Crew Chief account
  5. Owner assigns permissions

---

## DAQ Branding (Completed)
All RacePak-specific UI labels replaced with neutral DAQ terminology:
- "RacePak CSV" → "Run Data CSV"
- "RacePak Charts" → "Channel Charts"
- "RacePak Data" → "Run Data"
- "RacePak Controls" → "Run Data Controls"
- "RacePak Peaks" → "Channel Peaks"

**Compatibility note:**
- App supports any DAQ system that exports CSV with time-based channel data (AIM, Holley, MoTeC, etc.)
- Channel names are read dynamically from the CSV — not hardcoded
- **TODO:** Audit code for any hardcoded channel name logic (e.g., `if channel == 'Engine RPM'`) that could break for non-RacePak systems
- Users can reassign channels to groups via the All Channels table regardless of their DAQ brand

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
    - Build recalculate logic as a clean reusable function first — WeatherKit plugs in when approved

### Phase 2 — Paywall (target: Sep–Oct 2026)
**Can be built before multiple cars and team logins are ready.**

Trial behavior when expired (read-only mode):
- Can view existing runs ✅
- Cannot add new runs ❌
- Cannot upload new CSVs ❌
- ET Predictor visible but locked ❌
- Channel Charts visible but locked ❌
- Persistent banner: "Your trial has expired — upgrade to continue adding runs"

Card collection: after trial expires (not upfront) — friendlier for beta, lower conversion rate (~20-30% vs 60-70% upfront)

Launch sequence:
1. Set up Stripe with all three tiers in Sandbox — DONE ✅
2. Build 30-day trial logic
3. Build feature gating (Channel Charts hidden from Racer tier)
4. Add "coming soon" placeholder for Crew Chief-only features
5. Beta test full payment flow with fake card numbers
6. Flip Stripe from Sandbox to live mode
7. Move hosting from Streamlit Community Cloud to Railway or Render

Technical needs:
- `subscription_tier` field in user record (racer / pro / crew_chief)
- `trial_start_date` field
- Stripe webhook to update tier on payment/cancellation
- Feature gate: Channel Charts hidden for Racer tier

### Phase 2.5 — Referral Program
- Each user gets a unique referral code
- Mechanic: "Refer a friend who subscribes — you both get one free month"
- Built on Stripe's coupon/credit infrastructure
- Technical needs:
  - `referral_code` field per user in Supabase
  - Track successful referral conversions
  - Auto-apply free month credit via Stripe when referral subscribes

### Phase 3 — Multiple Cars + Teams (target: Q4 2026)
- Multiple cars (Crew Chief tier)
  - New `cars` table in Supabase
  - Car switcher in sidebar
  - Runs linked to specific car
  - ET Predictor filters by selected car
  - Natural point to split app.py into multiple files
- Team logins (Crew Chief tier)
  - `invites` table: token, owner account, email, expiry
  - `team_members` table: links team user to owner account + permissions
  - Invite email via SendGrid or Supabase Auth
  - Special invite landing page in app
  - Per-member permission management UI for owner
- Notify existing Crew Chief subscribers when features go live

### Phase 4 — Scaling (when user growth requires it)
See Scaling section below.

### Future Consideration
- Mobile-responsive web layout
- React Native mobile app (timeslip upload + ET predictor)
- Annual pricing option (2 months free)
- Stripe per-seat pricing for larger teams
- Physical weather station integration (Kestrel, Ambient Weather Network)
- YouTube video link field on runs
- UptimeRobot monitoring (verify still active)
- Audit hardcoded channel name logic for non-RacePak DAQ compatibility

---

## Scaling Plan

### Market Context
- ~40,000 drag racers in the US
- Target: 10% = ~4,000 users
- Streamlit Community Cloud not suitable for production — move to paid hosting at Phase 2 launch

### Revenue Projection (at 4,000 users)
| Tier | Est. Users | Monthly Revenue |
|---|---|---|
| Racer $9.99 | 1,000 | $9,990 |
| Pro $19.99 | 2,000 | $39,980 |
| Crew Chief $34.99 | 1,000 | $34,990 |
| **Total** | **4,000** | **~$85,000/mo** |

### Infrastructure
- Move to Railway, Render, or DigitalOcean at Phase 2 launch
- racefusion.com already owned and pointed — DNS update is 5 minutes
- Each active Streamlit session ~250MB RAM

| Server RAM | Concurrent Users | Approx Monthly Cost |
|---|---|---|
| 4GB | ~15 users | $24–40/mo |
| 8GB | ~30 users | $48–80/mo |
| 16GB | ~60 users | $96–160/mo |
| 32GB | ~120 users | $200–300/mo |

- Supabase Pro ($25/mo): upgrade at ~500 active users

### Scaling Trigger Points
- 100 users → monitor performance
- 500 users → upgrade Supabase to Pro, migrate to paid hosting
- 1,000 users → evaluate larger server or multiple instances
- 4,000 users → evaluate full infrastructure review

---

## Realistic Timeline
- **Aug 2026** → Apple WeatherKit live
- **Sep–Oct 2026** → Paywall + Stripe live, beta test, start charging, move to paid hosting
- **Q4 2026** → Multiple cars + team logins + referral program
- **Q1 2027** → 1,000 users target

---

## Security Constraints (permanent)
- Do NOT commit: .env file, runs/ folder, users/ folder
- API keys must NEVER be stored in racefusion_config.json or any file committed to GitHub
- API keys must never be shared in chats
- Stripe keys (pk_test_, sk_test_, pk_live_, sk_live_) go in .env file only
