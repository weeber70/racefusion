"""
car_profile.py — RaceFusion Car Profile page.

Shows a detailed build-sheet form for the user's car(s).
All fields are optional and stored as JSONB in the cars.build_sheet column.
"""
import streamlit as st
from database import get_user_cars, create_car, load_car_build_sheet, save_car_build_sheet


def show_car_profile(current_user: str, logo_src: "str | None" = None):
    """Render the Car Profile page."""
    if logo_src:
        st.markdown(
            f'<img src="{logo_src}" style="max-width:520px;width:60%;'
            f'margin:0 auto 4px auto;display:block;">',
            unsafe_allow_html=True,
        )

    st.markdown("## 🔧 Car Profile")
    st.markdown("---")

    # ── Car selector ──────────────────────────────────────────────────────────
    _cars = get_user_cars(current_user)

    if not _cars:
        st.info("No cars on file yet. Create one below to get started.")
        with st.form("create_car_form"):
            _new_name   = st.text_input("Car name", placeholder="e.g. '69 Camaro Pro Stock")
            _new_carnum = st.text_input("Car number (optional)", placeholder="e.g. 327K")
            if st.form_submit_button("➕ Create Car", type="primary"):
                if _new_name.strip():
                    _cid = create_car(current_user, _new_name.strip(), _new_carnum.strip())
                    if _cid:
                        st.success(f"Car '{_new_name.strip()}' created!")
                        st.rerun()
                else:
                    st.warning("Enter a car name first.")
        return

    _car_names = [c["car_name"] for c in _cars]
    _car_ids   = [c["car_id"]   for c in _cars]
    _saved_sel = st.session_state.get("cp_selected_car_idx", 0)
    _safe_sel  = min(_saved_sel, len(_car_names) - 1)

    if len(_cars) > 1:
        _sel_idx = st.selectbox(
            "Select car", options=range(len(_car_names)),
            format_func=lambda i: _car_names[i],
            index=_safe_sel, key="cp_car_selectbox",
        )
    else:
        _sel_idx = 0

    st.session_state["cp_selected_car_idx"] = _sel_idx
    _active_car_id = _car_ids[_sel_idx]

    # ── Load saved build sheet ────────────────────────────────────────────────
    _bs = load_car_build_sheet(_active_car_id)

    def _g(key, default=""):
        return _bs.get(key, default)

    def _opt(key, options, default=""):
        val = _bs.get(key, default)
        return val if val in options else default

    # ── Info banner ───────────────────────────────────────────────────────────
    st.info(
        "ℹ️ All fields are optional. The more you fill in, "
        "the more accurate your AI Tuner recommendations will be."
    )

    # ═════════════════════════════════════════════════════════════════════════
    # IDENTITY
    # ═════════════════════════════════════════════════════════════════════════
    st.subheader("Identity")
    _id_c1, _id_c2, _id_c3 = st.columns(3)
    _f_year  = _id_c1.text_input("Year",  value=_g("year"),  placeholder="e.g. 1969", key="cp_year")
    _f_make  = _id_c2.text_input("Make",  value=_g("make"),  placeholder="e.g. Chevrolet", key="cp_make")
    _f_model = _id_c3.text_input("Model", value=_g("model"), placeholder="e.g. Camaro", key="cp_model")

    _f_class = st.text_input(
        "Race Class", value=_g("race_class"),
        placeholder="e.g. Top Alcohol Funny Car, Bracket, Super Gas...",
        key="cp_race_class",
    )

    _wt_c1, _wt_c2 = st.columns(2)
    _f_wt_w  = _wt_c1.text_input(
        "Weight with driver (lbs)", value=_g("weight_with_driver"),
        placeholder="e.g. 2450", key="cp_wt_w",
    )
    _f_wt_wo = _wt_c2.text_input(
        "Weight without driver (lbs)", value=_g("weight_without_driver"),
        placeholder="e.g. 2250", key="cp_wt_wo",
    )

    # ═════════════════════════════════════════════════════════════════════════
    # ENGINE
    # ═════════════════════════════════════════════════════════════════════════
    st.subheader("Engine")
    _eng_c1, _eng_c2 = st.columns(2)
    _f_disp = _eng_c1.text_input(
        "Displacement (cubic inches)", value=_g("displacement"),
        placeholder="e.g. 540", key="cp_displacement",
    )

    _block_opts = ["", "Iron", "Aluminum"]
    _f_block = _eng_c2.selectbox(
        "Block material", options=_block_opts,
        index=_block_opts.index(_opt("block_material", _block_opts)),
        key="cp_block_material",
    )

    _f_head = st.text_input(
        "Cylinder head brand / model", value=_g("cylinder_head"),
        placeholder="e.g. Brodix Track 1, AFR 305", key="cp_cylinder_head",
    )
    _cr_c1, _cr_c2 = st.columns(2)
    _f_cr = _cr_c1.text_input(
        "Compression ratio", value=_g("compression_ratio"),
        placeholder="e.g. 12.5:1", key="cp_compression",
    )

    _vt_opts = ["", "Hydraulic Flat", "Hydraulic Roller", "Solid Flat", "Solid Roller"]
    _f_vt = _cr_c2.selectbox(
        "Valvetrain type", options=_vt_opts,
        index=_vt_opts.index(_opt("valvetrain_type", _vt_opts)),
        key="cp_valvetrain",
    )

    _f_cam = st.text_input(
        "Cam specs", value=_g("cam_specs"),
        placeholder="e.g. 236/242 .650/.660 110 LSA", key="cp_cam",
    )

    # ═════════════════════════════════════════════════════════════════════════
    # POWER ADDER
    # ═════════════════════════════════════════════════════════════════════════
    st.subheader("Power Adder")

    _pa_opts = ["None", "Roots/Screw Blower", "Turbo", "Nitrous"]
    # Migrate legacy values that are no longer valid options
    _pa_saved = _g("power_adder_type", "None")
    if _pa_saved not in _pa_opts:
        _pa_saved = "None"
    _f_pa_type = st.selectbox(
        "Type", options=_pa_opts,
        index=_pa_opts.index(_pa_saved),
        key="cp_pa_type",
    )

    if _f_pa_type == "Roots/Screw Blower":
        _bl_c1, _bl_c2 = st.columns(2)
        _f_pa_bl_brand = _bl_c1.text_input(
            "Brand", value=_g("bl_brand"),
            placeholder="e.g. Weiand, BDS, Magnuson", key="cp_bl_brand",
        )
        _f_pa_bl_size = _bl_c2.text_input(
            "Blower size", value=_g("bl_size"),
            placeholder="e.g. 14-71, 10-71", key="cp_bl_size",
        )
        _bl_c3, _bl_c4 = st.columns(2)
        _f_pa_bl_od = _bl_c3.text_input(
            "Overdrive %", value=_g("bl_overdrive_pct"),
            placeholder="e.g. 15", key="cp_bl_od",
        )
        _f_pa_bl_boost = _bl_c4.text_input(
            "Boost PSI", value=_g("bl_boost_psi"),
            placeholder="e.g. 22", key="cp_bl_boost",
        )
        _bl_c5, _bl_c6 = st.columns(2)
        _f_pa_bl_top = _bl_c5.text_input(
            "Top pulley size", value=_g("bl_top_pulley"),
            placeholder='e.g. 8"', key="cp_bl_top_pulley",
        )
        _f_pa_bl_bot = _bl_c6.text_input(
            "Bottom pulley size", value=_g("bl_bottom_pulley"),
            placeholder='e.g. 6.5"', key="cp_bl_bot_pulley",
        )
        _f_pa_bl_ic = st.checkbox(
            "Intercooled", value=bool(_bs.get("bl_intercooled", False)),
            key="cp_bl_intercooled",
        )

    elif _f_pa_type == "Turbo":
        _tr_c1, _tr_c2 = st.columns(2)
        _f_pa_tr_brand = _tr_c1.text_input(
            "Brand", value=_g("tr_brand"),
            placeholder="e.g. Precision, Garrett, BorgWarner", key="cp_tr_brand",
        )
        _f_pa_tr_frame = _tr_c2.text_input(
            "Frame size (mm)", value=_g("tr_frame_mm"),
            placeholder="e.g. 88mm", key="cp_tr_frame",
        )
        _tr_c3, _tr_c4 = st.columns(2)
        _st_opts = ["", "Single", "Twin"]
        _f_pa_tr_st = _tr_c3.selectbox(
            "Single / Twin", options=_st_opts,
            index=_st_opts.index(_opt("tr_single_twin", _st_opts)),
            key="cp_tr_single_twin",
        )
        _f_pa_tr_boost = _tr_c4.text_input(
            "Boost PSI", value=_g("tr_boost_psi"),
            placeholder="e.g. 28", key="cp_tr_boost",
        )
        _f_pa_tr_ic = st.checkbox(
            "Intercooled", value=bool(_bs.get("tr_intercooled", False)),
            key="cp_tr_intercooled",
        )

    elif _f_pa_type == "Nitrous":
        _no_c1, _no_c2 = st.columns(2)
        _f_pa_no_brand = _no_c1.text_input(
            "Brand", value=_g("no_brand"),
            placeholder="e.g. NOS, Zex, Edelbrock", key="cp_no_brand",
        )
        _dw_opts = ["", "Dry", "Wet"]
        _f_pa_no_dw = _no_c2.selectbox(
            "Dry / Wet", options=_dw_opts,
            index=_dw_opts.index(_opt("no_dry_wet", _dw_opts)),
            key="cp_no_dry_wet",
        )
        _no_c3, _no_c4 = st.columns(2)
        _f_pa_no_stages = _no_c3.text_input(
            "Number of stages", value=_g("no_stages"),
            placeholder="e.g. 2", key="cp_no_stages",
        )
        _f_pa_no_hp = _no_c4.text_input(
            "HP per stage", value=_g("no_hp_per_stage"),
            placeholder="e.g. 150", key="cp_no_hp",
        )

    # ═════════════════════════════════════════════════════════════════════════
    # FUEL SYSTEM
    # ═════════════════════════════════════════════════════════════════════════
    st.subheader("Fuel System")
    _fuel_opts = ["", "Gasoline", "Methanol", "E85", "Nitromethane", "Other"]
    _f_fuel_type = st.selectbox(
        "Fuel type", options=_fuel_opts,
        index=_fuel_opts.index(_opt("fuel_type", _fuel_opts)),
        key="cp_fuel_type",
    )

    # Fuel delivery / induction
    _fd_opts = ["", "Carburetor", "EFI", "Mechanical Fuel Injection"]
    _f_fuel_delivery = st.selectbox(
        "Fuel delivery / Induction", options=_fd_opts,
        index=_fd_opts.index(_opt("fuel_delivery", _fd_opts)),
        key="cp_fuel_delivery",
    )

    if _f_fuel_delivery == "Carburetor":
        _carb_c1, _carb_c2 = st.columns(2)
        _f_carb_brand = _carb_c1.text_input(
            "Carb brand", value=_g("carb_brand"),
            placeholder="e.g. Holley, Demon, Quick Fuel", key="cp_carb_brand",
        )
        _f_carb_cfm = _carb_c2.text_input(
            "CFM rating", value=_g("carb_cfm"),
            placeholder="e.g. 1050", key="cp_carb_cfm",
        )
        _carb_count_opts = ["", "1", "2", "3x2", "4"]
        _f_carb_count = st.selectbox(
            "Number of carbs", options=_carb_count_opts,
            index=_carb_count_opts.index(_opt("carb_count", _carb_count_opts)),
            key="cp_carb_count",
        )

    elif _f_fuel_delivery == "EFI":
        _efi_c1, _efi_c2 = st.columns(2)
        _f_efi_brand = _efi_c1.text_input(
            "System brand", value=_g("efi_brand"),
            placeholder="e.g. FAST, Holley HP, FiTech", key="cp_efi_brand",
        )
        _f_efi_inj_size = _efi_c2.text_input(
            "Injector size (lb/hr)", value=_g("efi_injector_lb_hr"),
            placeholder="e.g. 120", key="cp_efi_inj_size",
        )
        _f_efi_num_inj = st.text_input(
            "Number of injectors", value=_g("efi_num_injectors"),
            placeholder="e.g. 8", key="cp_efi_num_inj",
        )

    elif _f_fuel_delivery == "Mechanical Fuel Injection":
        _mfi_c1, _mfi_c2 = st.columns(2)
        _f_mfi_brand = _mfi_c1.text_input(
            "Brand", value=_g("mfi_brand"),
            placeholder="e.g. Hilborn, Enderle, Kinsler", key="cp_mfi_brand",
        )
        _f_mfi_hat = _mfi_c2.text_input(
            "Hat size", value=_g("mfi_hat_size"),
            placeholder='e.g. 2", 2.5"', key="cp_mfi_hat",
        )
        _f_mfi_nozzle = st.text_input(
            "Nozzle configuration", value=_g("mfi_nozzle_config"),
            placeholder="e.g. 8-port w/ pill, main jets + bypass", key="cp_mfi_nozzle",
        )

    _fuel_c1, _fuel_c2 = st.columns(2)
    _f_fuel_pump = _fuel_c1.text_input(
        "Fuel pump brand / model", value=_g("fuel_pump"),
        placeholder="e.g. Weldon 2025, Aeromotive A1000", key="cp_fuel_pump",
    )
    _f_fuel_psi = _fuel_c2.text_input(
        "Fuel pressure (PSI)", value=_g("fuel_pressure_psi"),
        placeholder="e.g. 6.5", key="cp_fuel_pressure",
    )

    # ═════════════════════════════════════════════════════════════════════════
    # IGNITION
    # ═════════════════════════════════════════════════════════════════════════
    st.subheader("Ignition")
    _ign_sys_opts = ["", "MSD", "Mallory", "Crane", "Distributor", "CDI", "Other"]
    _f_ign_sys = st.selectbox(
        "System type", options=_ign_sys_opts,
        index=_ign_sys_opts.index(_opt("ignition_system", _ign_sys_opts)),
        key="cp_ign_sys",
    )
    _ign_c1, _ign_c2 = st.columns(2)
    _f_ign_init = _ign_c1.text_input(
        "Initial timing (degrees)", value=_g("timing_initial"),
        placeholder="e.g. 16", key="cp_ign_initial",
    )
    _f_ign_total = _ign_c2.text_input(
        "Total timing (degrees)", value=_g("timing_total"),
        placeholder="e.g. 34", key="cp_ign_total",
    )
    _ign_c3, _ign_c4 = st.columns(2)
    _f_plug_brand = _ign_c3.text_input(
        "Spark plug brand", value=_g("spark_plug_brand"),
        placeholder="e.g. NGK, Autolite, Denso", key="cp_plug_brand",
    )
    _f_plug_heat = _ign_c4.text_input(
        "Heat range", value=_g("spark_plug_heat_range"),
        placeholder="e.g. R5671A-8", key="cp_plug_heat",
    )

    # ═════════════════════════════════════════════════════════════════════════
    # DRIVETRAIN
    # ═════════════════════════════════════════════════════════════════════════
    st.subheader("Drivetrain")
    _trans_opts = ["", "Powerglide", "TH400", "Turbo 350", "Lenco", "Manual", "Other"]
    _f_trans = st.selectbox(
        "Transmission type", options=_trans_opts,
        index=_trans_opts.index(_opt("transmission_type", _trans_opts)),
        key="cp_transmission",
    )

    # Default number-of-gears based on trans type
    _num_gears_default = _bs.get("num_gears", None)
    if _num_gears_default is None:
        if _f_trans == "Powerglide":
            _num_gears_default = 2
        elif _f_trans == "Lenco":
            _num_gears_default = 3
        else:
            _num_gears_default = 2
    try:
        _num_gears_default = int(_num_gears_default)
    except (ValueError, TypeError):
        _num_gears_default = 2
    _num_gears_default = max(1, min(6, _num_gears_default))

    _gear_count_opts = [1, 2, 3, 4, 5, 6]
    _f_num_gears = st.selectbox(
        "Number of gears", options=_gear_count_opts,
        index=_gear_count_opts.index(_num_gears_default),
        key="cp_num_gears",
    )

    _saved_gear_ratios = _bs.get("gear_ratios", {})
    _gear_labels = ["1st", "2nd", "3rd", "4th", "5th", "6th"]
    _gear_cols = st.columns(min(_f_num_gears, 3))
    _f_gear_ratios = {}
    for _gi in range(_f_num_gears):
        _col = _gear_cols[_gi % len(_gear_cols)]
        _f_gear_ratios[str(_gi + 1)] = _col.text_input(
            f"{_gear_labels[_gi]} gear ratio",
            value=str(_saved_gear_ratios.get(str(_gi + 1), "")),
            placeholder="e.g. 1.76",
            key=f"cp_gear_{_gi + 1}",
        )

    _dt_c1, _dt_c2 = st.columns(2)
    _f_conv = _dt_c1.text_input(
        "Converter brand / model", value=_g("converter"),
        placeholder="e.g. Neal Chance 3600 stall", key="cp_converter",
    )
    _f_stall = _dt_c2.text_input(
        "Stall speed (RPM)", value=_g("stall_rpm"),
        placeholder="e.g. 3800", key="cp_stall",
    )
    _dt_c3, _dt_c4 = st.columns(2)
    _f_rear_ratio = _dt_c3.text_input(
        "Rear gear ratio", value=_g("rear_gear_ratio"),
        placeholder="e.g. 4.11", key="cp_rear_ratio",
    )
    _rear_end_opts = ["", "Spool", "Posi", "Limited Slip", "Open"]
    _f_rear_end = _dt_c4.selectbox(
        "Rear end type", options=_rear_end_opts,
        index=_rear_end_opts.index(_opt("rear_end_type", _rear_end_opts)),
        key="cp_rear_end",
    )

    # ═════════════════════════════════════════════════════════════════════════
    # TIRES & WHEELS
    # ═════════════════════════════════════════════════════════════════════════
    st.subheader("Tires & Wheels")
    _tire_c1, _tire_c2, _tire_c3 = st.columns(3)
    _f_front_tire = _tire_c1.text_input(
        "Front tire size", value=_g("front_tire_size"),
        placeholder="e.g. 26x4.5-15", key="cp_front_tire",
    )
    _f_rear_slick = _tire_c2.text_input(
        "Rear slick size", value=_g("rear_slick_size"),
        placeholder="e.g. 36 x 17 - 16", key="cp_rear_slick",
    )
    _f_rollout = _tire_c3.text_input(
        "Rollout (inches)", value=_g("rollout_inches"),
        placeholder="e.g. 90.5", key="cp_rollout",
    )

    # ═════════════════════════════════════════════════════════════════════════
    # CHASSIS & SUSPENSION
    # ═════════════════════════════════════════════════════════════════════════
    st.subheader("Chassis & Suspension")
    _rear_susp_opts = [
        "", "Hardtail", "4-link", "Ladder bar", "Leaf spring",
        "Torque arm", "None/Funny Car/Rail",
    ]
    _front_susp_opts = [
        "", "Stock/OEM", "Tubular aftermarket", "Strut", "None/Funny Car/Rail",
    ]
    _ch_c1, _ch_c2 = st.columns(2)
    _f_rear_susp = _ch_c1.selectbox(
        "Rear suspension", options=_rear_susp_opts,
        index=_rear_susp_opts.index(_opt("rear_suspension", _rear_susp_opts)),
        key="cp_rear_susp",
    )
    _f_front_susp = _ch_c2.selectbox(
        "Front suspension", options=_front_susp_opts,
        index=_front_susp_opts.index(_opt("front_suspension", _front_susp_opts)),
        key="cp_front_susp",
    )
    _f_chassis = st.text_input(
        "Chassis type", value=_g("chassis_type"),
        placeholder="e.g. Funny Car, Altered, Rail, tube-frame stock", key="cp_chassis",
    )

    # ═════════════════════════════════════════════════════════════════════════
    # SAVE
    # ═════════════════════════════════════════════════════════════════════════
    st.markdown("---")
    if st.button("💾 Save Car Profile", type="primary", key="cp_save_btn"):
        _build_sheet: dict = {
            # Identity
            "year":                  _f_year.strip(),
            "make":                  _f_make.strip(),
            "model":                 _f_model.strip(),
            "race_class":            _f_class.strip(),
            "weight_with_driver":    _f_wt_w.strip(),
            "weight_without_driver": _f_wt_wo.strip(),
            # Engine
            "displacement":          _f_disp.strip(),
            "block_material":        _f_block,
            "cylinder_head":         _f_head.strip(),
            "compression_ratio":     _f_cr.strip(),
            "valvetrain_type":       _f_vt,
            "cam_specs":             _f_cam.strip(),
            # Power adder
            "power_adder_type":      _f_pa_type,
        }

        if _f_pa_type == "Roots/Screw Blower":
            _build_sheet.update({
                "bl_brand":         _f_pa_bl_brand.strip(),
                "bl_size":          _f_pa_bl_size.strip(),
                "bl_overdrive_pct": _f_pa_bl_od.strip(),
                "bl_boost_psi":     _f_pa_bl_boost.strip(),
                "bl_top_pulley":    _f_pa_bl_top.strip(),
                "bl_bottom_pulley": _f_pa_bl_bot.strip(),
                "bl_intercooled":   _f_pa_bl_ic,
            })
        elif _f_pa_type == "Turbo":
            _build_sheet.update({
                "tr_brand":       _f_pa_tr_brand.strip(),
                "tr_frame_mm":    _f_pa_tr_frame.strip(),
                "tr_single_twin": _f_pa_tr_st,
                "tr_boost_psi":   _f_pa_tr_boost.strip(),
                "tr_intercooled": _f_pa_tr_ic,
            })
        elif _f_pa_type == "Nitrous":
            _build_sheet.update({
                "no_brand":        _f_pa_no_brand.strip(),
                "no_dry_wet":      _f_pa_no_dw,
                "no_stages":       _f_pa_no_stages.strip(),
                "no_hp_per_stage": _f_pa_no_hp.strip(),
            })

        # Fuel delivery conditional fields
        _build_sheet["fuel_delivery"] = _f_fuel_delivery
        if _f_fuel_delivery == "Carburetor":
            _build_sheet.update({
                "carb_brand": _f_carb_brand.strip(),
                "carb_cfm":   _f_carb_cfm.strip(),
                "carb_count": _f_carb_count,
            })
        elif _f_fuel_delivery == "EFI":
            _build_sheet.update({
                "efi_brand":           _f_efi_brand.strip(),
                "efi_injector_lb_hr":  _f_efi_inj_size.strip(),
                "efi_num_injectors":   _f_efi_num_inj.strip(),
            })
        elif _f_fuel_delivery == "Mechanical Fuel Injection":
            _build_sheet.update({
                "mfi_brand":         _f_mfi_brand.strip(),
                "mfi_hat_size":      _f_mfi_hat.strip(),
                "mfi_nozzle_config": _f_mfi_nozzle.strip(),
            })

        _build_sheet.update({
            # Fuel system
            "fuel_type":             _f_fuel_type,
            "fuel_pump":             _f_fuel_pump.strip(),
            "fuel_pressure_psi":     _f_fuel_psi.strip(),
            # Ignition
            "ignition_system":       _f_ign_sys,
            "timing_initial":        _f_ign_init.strip(),
            "timing_total":          _f_ign_total.strip(),
            "spark_plug_brand":      _f_plug_brand.strip(),
            "spark_plug_heat_range": _f_plug_heat.strip(),
            # Drivetrain
            "transmission_type":     _f_trans,
            "num_gears":             _f_num_gears,
            "gear_ratios":           {k: v.strip() for k, v in _f_gear_ratios.items()},
            "converter":             _f_conv.strip(),
            "stall_rpm":             _f_stall.strip(),
            "rear_gear_ratio":       _f_rear_ratio.strip(),
            "rear_end_type":         _f_rear_end,
            # Tires & Wheels
            "front_tire_size":       _f_front_tire.strip(),
            "rear_slick_size":       _f_rear_slick.strip(),
            "rollout_inches":        _f_rollout.strip(),
            # Chassis & Suspension
            "rear_suspension":       _f_rear_susp,
            "front_suspension":      _f_front_susp,
            "chassis_type":          _f_chassis.strip(),
        })

        if save_car_build_sheet(_active_car_id, _build_sheet):
            st.success("Car profile saved!")
