"""
instructions.py — RaceFusion Instructions / Help page.
"""
import streamlit as st


def show_instructions(logo_src: "str | None" = None):
    """Render the Instructions page."""
    if logo_src:
        st.markdown(
            f'<img src="{logo_src}" style="max-width:520px;width:60%;'
            f'margin:0 auto 4px auto;display:block;">',
            unsafe_allow_html=True,
        )
    else:
        st.markdown("## 🏁 RaceFusion")

    st.markdown("# 📖 How to Use RaceFusion")
    st.markdown(
        "<p style='color:#888;margin-top:-12px;'>Everything you need to get the most out of your data.</p>",
        unsafe_allow_html=True,
    )
    st.markdown("---")

    # ── Creating a Run ────────────────────────────────────────────────────────
    st.subheader("🆕 Creating a Run")
    st.markdown("""
RaceFusion gives you three ways to create a run — use whichever data you have available:

**CSV only** — Upload your data logger export without a timeslip. You'll get the full channel overlay, RPM analysis, shift point detection, EGT spread, and RWHP estimate. Timeslip data (ET, MPH, reaction time) can be added later from the run view.

**Timeslip only** — Upload a photo of your printed timeslip without a CSV. RaceFusion will OCR the slip and extract ET, MPH, reaction time, 60ft, and more. Channel data can be attached later.

**Both** — The most complete picture: channel data from your logger combined with the printed timeslip numbers and auto-fetched weather for the run date.

To create a run, navigate to **Run Analysis** and use the **Create New Run** form. Select your car, enter the run date and time, attach your files, and click Save. Any missing data — CSV, timeslip, weather, or run details — can be added at any time by opening the run later.
""")
    st.markdown("---")

    # ── Run Data CSV ─────────────────────────────────────────────────────────
    st.subheader("📂 Run Data CSV")
    st.markdown("""
The Run Data CSV is an ASCII export from your on-car data logger. RaceFusion is designed around **RacePak** DataLink II exports, but any comma-delimited time-series file works as long as the first column is time in seconds.

**How to export from RacePak DataLink II:**

1. Open your run in DataLink II
2. Make all desired channels active
3. Go to **File → Print/Save ASCII File**
4. In the dialog: set Column Delimiter to **Comma**, Sampling Interval to **0.02**, New Page Every to **0 Lines**
5. Leave "Directly Print in ASCII (no preview)" unchecked, then click OK and save the file
6. Upload the saved `.csv` file in the **Run Data** section of the sidebar

**What channels are read:** RPM, throttle position, fuel pressure, fuel flow, all EGT channels (EGT1–EGT8), boost, drive shaft speed, and any other channels in your file. RaceFusion auto-groups channels into Fuel, EGT, RPM, Boost, and Other tabs. Unrecognized channels appear in the Other tab and can still be graphed.

**File format requirements:** The file must be plain text, comma-delimited, with a header row containing channel names. The time column must be first. There is no strict file-size limit, but very long runs may be slower to load.
""")
    st.markdown("---")

    # ── Timeslip Photo ────────────────────────────────────────────────────────
    st.subheader("🎫 Timeslip Photo")
    st.markdown("""
RaceFusion uses AI to read your printed timeslip from a photo.

**How to get a good scan:**
- Lay the timeslip flat on a solid dark surface
- Photograph straight-on (not at an angle) with good lighting — avoid shadows across the text
- Use your phone camera at close range; the timeslip should fill most of the frame
- JPG, PNG, and WEBP are all accepted

**What is extracted:** ET (quarter-mile or eighth-mile), MPH, reaction time, 60ft, 330ft, 660ft, 1000ft, track name, date, time of day, lane, car number, and dial-in where printed.

**Car number requirement:** RaceFusion needs your car number to identify which lane belongs to you on a dual-lane timeslip. Set your car number once in the **Car Profile** section of the sidebar.

**Re-scanning:** If the initial scan returned wrong data (wrong lane, misread digits), open the run in Run Analysis, scroll to the Timeslip section, and use the **Re-scan** button. You can also manually override any field directly in the run record.
""")
    st.markdown("---")

    # ── Run Details ───────────────────────────────────────────────────────────
    st.subheader("📋 Run Details")
    st.markdown("""
Run Details let you capture the setup used on each pass — tire pressures, tune, and notes. This data is used by the AI Tuner analysis and the run-to-run diff (Changes from Last Run).

**Fields and why they matter:**

- **Tire Pressures (FL/FR/RL/RR)** — Front-to-rear and side-to-side balance affects weight transfer and 60ft times. Track changes over a day of racing.
- **Track / Tire Temp** — Grip level varies with surface and rubber temperature. Useful for explaining unexpected 60ft variation.
- **Launch RPM / Shift Point** — Baseline launch and shift settings so you can correlate RPM changes with ET delta between runs.
- **Fuel (Main Jet, HS Jet, HS Open PSI)** — Carburetor jetting logged per pass. The AI Tuner uses this when analyzing EGT spread.
- **Blower (Top/Bottom Pulley)** — Overdrive % is auto-calculated. Track pulley changes and their effect on ET.
- **Wheelie Bar (D / P)** — Bar position logged in thousandths of an inch. Small changes have a meaningful effect on 60ft.
- **Ignition (Plug, Gap, Valve Lash)** — Maintenance tracking for ignition components.
- **Run Notes** — Free-text field for anything else: track conditions, weather feel, car behavior, what to try next.

Click **Save Run Details** to commit. The expander collapses automatically after saving, and the Changes from Last Run panel on the right will diff the saved values against the previous run for the same car.
""")
    st.markdown("---")

    # ── Weather & Density Altitude ────────────────────────────────────────────
    st.subheader("🌤️ Weather & Density Altitude")
    st.markdown("""
When a timeslip is scanned, RaceFusion automatically fetches historical weather for the run date, time, and track location from the Open-Meteo archive. No manual entry is needed.

**What is fetched:** Temperature (°F), barometric pressure (inHg), relative humidity (%), and wind speed/direction at the time of your run.

**Density Altitude (DA)** is the altitude at which the air behaves as if it were at standard sea-level conditions. It accounts for temperature, pressure, and humidity together. Lower DA = denser air = more oxygen = better ET. Higher DA = thinner air = slower ET.

As a rule of thumb, every ~1,000 ft increase in DA costs approximately 1% in ET. RaceFusion uses DA as a key input for ET predictions in the Race Day Predictor.

**If weather is missing:** The fetch requires a known track location. Make sure your track name or location is set in the **Location** section of the sidebar. You can also set your location manually and re-fetch weather for a run from the run view.
""")
    st.markdown("---")

    # ── Race Day Predictor ────────────────────────────────────────────────────
    st.subheader("🏁 Race Day Predictor")
    st.markdown("""
The Race Day Predictor estimates your ET for today's conditions based on your run history and today's weather.

**How it works:**

1. RaceFusion fetches today's weather at your track location
2. It pulls your recent runs that have both ET and DA recorded
3. It fits a linear regression through (DA, ET) pairs, then reads off the predicted ET at today's DA
4. An IQR filter removes statistical outliers before fitting so a single bad run doesn't skew the prediction

**"Not enough runs"** — The predictor needs at least 3 runs with both ET and DA data to fit a line. Add more runs or make sure existing runs have timeslips and weather attached.

**Including / Excluding Runs** — In the run history table, each run has an Include/Exclude toggle. Use this to remove runs from a day with unusual conditions (wet track, mechanical issue, tune experiment) without deleting them. The model only uses included runs.

**Dial-in suggestion** — The predictor shows a suggested dial-in based on the predicted ET. Adjust your actual dial-in based on track conditions, driver confidence, and how well the prediction has been tracking lately.
""")
    st.markdown("---")

    # ── Season Summary ────────────────────────────────────────────────────────
    st.subheader("📅 Season Summary")
    st.markdown("""
Season Summary aggregates your saved runs by year and shows overall and per-season performance stats.

**Stats tracked:**

- **Total Runs** — All runs with a logged date for the selected season
- **Best ET** — Lowest recorded quarter-mile elapsed time
- **Best MPH** — Highest recorded trap speed
- **Best 60ft** — Lowest 60-foot time (indicator of launch performance)
- **Best Reaction Time** — Lowest reaction time across all runs
- **Win / Loss / Bye Record** — Tallied from the Result field in Run Details; win percentage shown
- **DA at time of run** — Density altitude for each run, so you can see which ET was run in the best air

**Run Log** — A chronological table of all runs in the selected season with per-cell highlights: gold for best ET, green for best reaction, blue for best 60ft.

**Runs by Track** — A breakdown of run count and best ET at each track you've visited.

Records are determined from timeslip data. Runs without timeslips do not contribute to ET, MPH, or reaction time records.
""")
    st.markdown("---")

    # ── Run Manager ───────────────────────────────────────────────────────────
    st.subheader("🗂️ Run Manager")
    st.markdown("""
Run Manager gives you a bird's-eye view of all your saved runs, organized by event (date + track).

**Searching** — Type in the search bar to filter by track name, date, or event name. Results update instantly.

**Event groups** — Runs from the same date and track are automatically grouped into one event. Each group shows the event name (if set), date, track, and run count.

**Naming events** — Click into the event name field inside any group and type a name (e.g. "Midwest Drags Bracket Points 1"). The name is saved immediately on blur and appears in the run selector and export.

**Selecting and deleting runs** — Check individual runs or use the event-level checkbox to select all runs in a group. The trash icon activates when at least one run is checked. Confirm the deletion prompt to permanently remove the selected runs and their associated files (CSV, timeslip image). This cannot be undone.

**Opening a run** — Click **▶ Open** on any row to jump directly to that run in Run Analysis.
""")
    st.markdown("---")

    st.markdown(
        "<p style='color:#555;font-size:0.8rem;text-align:center;margin-top:8px;'>"
        "RaceFusion — built for bracket racers, by bracket racers."
        "</p>",
        unsafe_allow_html=True,
    )
