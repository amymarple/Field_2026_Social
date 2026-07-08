"""
app.py — Episode Browser UI (Streamlit). VIEW ONLY: every data operation lives in
utils/ (episode_io, query, coverage, load_layout, annotations, validation,
video_preview) so this file can be swapped for a faster frontend without moving logic.

Layout: a three-region dashboard —
  * left    : brand header + nav + filters + a "current slice" info card (sidebar)
  * centre  : Episodes table (click a row to inspect) + Timeline / Field map /
              Coverage-QC panels
  * right   : Episode Detail (metadata, state vector, source evidence, lens scores,
              notes, quick verdict actions)

Lightness by design (don't load everything at once):
  * the store loads once, cached;
  * the browser opens on a SHORT initial time window (a small slice);
  * the table shows compact scalar summaries; full nested detail is materialized
    only for the one selected episode; video frames are subsampled on demand.

Run:  streamlit run app.py
Needs: streamlit (+ pyarrow to read the Parquet store; JSONL works without it;
       ffmpeg for the optional video preview).
"""
from __future__ import annotations

import colorsys
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from utils import (annotations, coverage, episode_io, load_layout, query,  # noqa: E402
                   video_preview, weather, wiser_tracks)

DATA_DIR = HERE / "data"
PARQUET = DATA_DIR / "synthetic_episodes.parquet"
JSONL = DATA_DIR / "synthetic_episodes.jsonl"
GAPS = DATA_DIR / "coverage_gaps.jsonl"
INITIAL_WINDOW_MIN = 60   # opens on a slice that includes the real 6/30 ~17:20 rain event

# Field-local time. Episode times are UTC; the field (and weather + observations) use
# EDT wall-clock. Day 1 = release day / epoch (see FIELD_OBSERVATIONS.md).
EDT_TZ = "Etc/GMT+4"          # fixed UTC-4 (POSIX sign flip); June is EDT
FIELD_DAY1 = pd.Timestamp("2026-06-28")

# Real shelter centres (field cm) from field_layout.json — for the "shelter-distance" readout.
SHELTER_PTS = [(342.6, 304.8), (881.5, 302.4)]
LABEL_COLORS = {
    "shelter_entry": "#1a7f5a", "rest_like": "#6b7280", "social_proximity": "#7c3aed",
    "group_convergence": "#b45309", "rain_response": "#2563eb", "following": "#0891b2",
    "possible_artifact": "#b91c1c",
}

st.set_page_config(page_title="Episode Browser", layout="wide", page_icon="🐀",
                   initial_sidebar_state="expanded")

st.markdown("""
<style>
  .block-container {padding-top: 1.1rem; padding-bottom: 1rem;}
  .app-header {display:flex; align-items:center; gap:14px; padding:2px 2px 12px;
               border-bottom:1px solid #e5e7eb; margin-bottom:14px;}
  .app-header .logo {font-size:26px;}
  .app-header .title {font-size:20px; font-weight:700; color:#0f172a;}
  .app-header .subtitle {color:#64748b; font-size:13px; margin-left:2px;}
  .app-header .badge {background:#eef2ff; color:#4338ca; border:1px solid #c7d2fe;
               padding:3px 10px; border-radius:14px; font-size:12px; font-weight:600;}
  .app-header .spacer {flex:1;}
  .app-header .icons {color:#64748b; font-size:13px;}
  .chip {display:inline-block; padding:2px 9px; margin:2px 4px 2px 0; border-radius:12px;
         font-size:12px; font-weight:600; color:#fff;}
  .card-title {font-weight:700; color:#0f172a; font-size:15px; margin-bottom:2px;}
  .kv {display:flex; justify-content:space-between; font-size:13px; padding:2px 0;
       border-bottom:1px dashed #eef2f6;}
  .kv .k {color:#64748b;} .kv .v {color:#0f172a; font-weight:600;}
  .infocard {background:#eff6ff; border:1px solid #bfdbfe; border-radius:10px;
             padding:10px 12px; font-size:12px; color:#1e3a8a;}
  div[data-testid="stMetricValue"] {font-size:20px;}
</style>
""", unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Cached loaders (data layer; each returns plain data, no widgets)
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False)
def load_store() -> pd.DataFrame:
    if PARQUET.exists():
        try:
            return episode_io.read_parquet(PARQUET)
        except Exception:
            pass
    if JSONL.exists():
        return episode_io.read_jsonl(JSONL)
    return pd.DataFrame()


@st.cache_data(show_spinner=False)
def load_gaps() -> pd.DataFrame:
    return episode_io.read_jsonl(GAPS) if GAPS.exists() else pd.DataFrame()


@st.cache_data(show_spinner=False)
def subject_names() -> dict:
    return load_layout.subject_name_map()


@st.cache_data(show_spinner=True, max_entries=64)
def cached_frames(path: str, start_s: float, end_s: float, n: int, width: int, mtime: float):
    return video_preview.extract_frames(path, start_s, end_s, n=n, width=width)


@st.cache_data(show_spinner=False)
def load_weather_cached() -> pd.DataFrame:
    return weather.load_weather()


@st.cache_data(show_spinner="Loading real WISER day…", max_entries=3)
def wiser_day_cached(date_str: str) -> pd.DataFrame:
    return wiser_tracks.read_day(date_str)


def real_wiser_window(t0_ms: int, t1_ms: int, shortids, max_per_rat: int = 400) -> pd.DataFrame:
    """Real WISER positions in [t0,t1] (inches). Reads the first day-file that covers it."""
    for d in wiser_tracks.candidate_dates(t0_ms, t1_ms):
        day = wiser_day_cached(d)
        if day.empty:
            continue
        win = wiser_tracks.filter_window(day, t0_ms, t1_ms, subject_names(),
                                         shortids or None, max_per_rat)
        if not win.empty:
            return win
    return pd.DataFrame(columns=["shortid", "rat", "x", "y", "ts", "calc_err"])


@st.cache_data(show_spinner=False)
def field_geometry() -> dict:
    """Paddock outline + poles + shelter footprints (cm) from field_layout.json.

    Falls back to bare bounds + approx shelters when the layout file is absent, so the
    field map always draws something.
    """
    layout = load_layout.load_field_layout()
    fc = (layout or {}).get("field_cm", {}) if layout else {}
    xlen = float(fc.get("x_len_40ft", 1219.2))
    ylen = float(fc.get("y_width_20ft", 609.6))
    poles = []
    for name, xy in ((layout or {}).get("poles", {}) or {}).items():
        if isinstance(xy, list) and len(xy) == 2:
            poles.append({"x": xy[0], "y": xy[1], "name": name})
    shelters = []
    for key, sh in ((layout or {}).get("shelters", {}) or {}).items():
        if not isinstance(sh, dict) or "center_cm" not in sh:
            continue
        cx, cy = sh["center_cm"]
        w, h = sh.get("size_cm", [62.55, 45.72])
        if float(sh.get("orientation_deg", 0)) % 180 == 90:   # rotated footprint
            w, h = h, w
        shelters.append({"x0": cx - w / 2, "x1": cx + w / 2,
                         "y0": cy - h / 2, "y1": cy + h / 2,
                         "name": f"Shelter {key}"})
    if not shelters:
        shelters = [{"x0": sx - 30, "x1": sx + 30, "y0": sy - 20, "y1": sy + 20,
                     "name": f"Shelter {i + 1}"} for i, (sx, sy) in enumerate(SHELTER_PTS)]
    return {"xlen": xlen, "ylen": ylen,
            "poles": pd.DataFrame(poles), "shelters": pd.DataFrame(shelters)}


def edt(ms):
    """UTC epoch-ms -> field-local (EDT) tz-aware Timestamp."""
    return pd.to_datetime(int(ms), unit="ms", utc=True).tz_convert(EDT_TZ)


def edt_naive(ms):
    """Field-local wall time, tz-naive — the axis weather is aligned on."""
    return edt(ms).tz_localize(None)


def fmt_ts(ms) -> str:
    if pd.isna(ms):
        return "—"
    return edt(ms).strftime("%H:%M:%S")


def fmt_date(ms) -> str:
    return "—" if pd.isna(ms) else edt(ms).strftime("%Y-%m-%d")


def day_label(ms) -> str:
    if pd.isna(ms):
        return "—"
    d = edt_naive(ms).normalize()
    return f"Day {(d - FIELD_DAY1).days + 1}"


def fmt_dur(s) -> str:
    if pd.isna(s):
        return "—"
    s = int(s)
    return f"{s // 60:02d}:{s % 60:02d}"


def names_of(subject_ids, nm) -> str:
    if not isinstance(subject_ids, list):
        return "—"
    return ", ".join(load_layout.resolve_subject(s, nm) for s in subject_ids)


def top_zone(z) -> str:
    if not isinstance(z, dict) or not z:
        return "—"
    k = max(z, key=z.get)
    return f"{k} ({z[k]:.2f})"


def top_label(labels) -> str:
    return labels[0] if isinstance(labels, list) and labels else "—"


def lens_rank(ep) -> float:
    """Max lens score, or NaN when unscored — NEVER 0 (absence is first-class)."""
    ls = ep.get("lens_scores")
    vals = [v for v in ls.values() if v is not None] if isinstance(ls, dict) else []
    return max(vals) if vals else float("nan")


def chips_html(labels) -> str:
    if not isinstance(labels, list) or not labels:
        return "<span style='color:#94a3b8;font-size:12px'>no labels</span>"
    out = []
    for lab in labels:
        out.append(f"<span class='chip' style='background:{LABEL_COLORS.get(lab, '#64748b')}'>{lab}</span>")
    return "".join(out)


# Per-rat hue (stable identity) + time as a lightness gradient of that hue.
RAT_HUES = [0.58, 0.08, 0.33, 0.92, 0.75]   # blue, orange, green, magenta, purple (0–1)


def rat_hue_map(rats) -> dict:
    """{rat -> hue} over sorted names, so a rat keeps the same hue across windows/focus."""
    rs = sorted(rats)
    return {r: RAT_HUES[i % len(RAT_HUES)] for i, r in enumerate(rs)}


def hex_from_hue(hue: float, tnorm: float) -> str:
    """Rat hue at a time-driven lightness (earlier = lighter, later = darker)."""
    t = 0.0 if tnorm != tnorm else max(0.0, min(1.0, tnorm))   # NaN-safe clamp
    light = 0.80 - 0.50 * t
    r, g, b = colorsys.hls_to_rgb(hue, light, 0.60)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def kpi_html(pairs) -> str:
    """Small 2-col KPI grid (avoids a 3rd column-nesting level Streamlit forbids)."""
    cells = "".join(
        f"<div style='flex:1 1 42%;background:#f8fafc;border:1px solid #eef2f6;"
        f"border-radius:8px;padding:8px 10px;margin:3px'>"
        f"<div style='font-size:11px;color:#64748b'>{k}</div>"
        f"<div style='font-size:19px;font-weight:700;color:#0f172a'>{v}</div></div>"
        for k, v in pairs)
    return f"<div style='display:flex;flex-wrap:wrap'>{cells}</div>"


def shelter_distance(sv) -> float | None:
    if not isinstance(sv, dict) or sv.get("x") is None:
        return None
    x, y = sv["x"], sv["y"]
    return round(min(((x - sx) ** 2 + (y - sy) ** 2) ** 0.5 for sx, sy in SHELTER_PTS), 1)


# --------------------------------------------------------------------------- #
df = load_store()
gaps_df = load_gaps()
nm = subject_names()

if df.empty:
    st.error("No episode store found. Run `python generate_synthetic_episodes.py` first.")
    st.stop()

for k, v in {"sel_id": None, "nav": "Dashboard",
             "session_tag": pd.Timestamp.utcnow().strftime("%Y%m%dT%H%M%S")}.items():
    st.session_state.setdefault(k, v)

opts = query.available_values(df)
t_min, t_max = int(df["t_start"].min()), int(df["t_end"].max())

# =========================================================================== #
# Header
# =========================================================================== #
_who = st.session_state.get("annotator_id", "").strip()
st.markdown(
    "<div class='app-header'>"
    "<span class='logo'>🐀</span>"
    "<span class='title'>Episode Browser</span>"
    "<span class='subtitle'>Field behavior toolbox</span>"
    "<span class='badge'>Prototype — synthetic episode demo</span>"
    "<span class='spacer'></span>"
    f"<span class='icons'>🧑 {_who if _who else 'no annotator ID set'}</span>"
    "</div>", unsafe_allow_html=True)


def _set_query(value: str) -> None:
    st.session_state.search_q = value


def _go_video(episode_id: str) -> None:
    st.session_state.sel_id = episode_id
    st.session_state.nav = "Video"


# =========================================================================== #
# Sidebar — nav + filters + info card
# =========================================================================== #
with st.sidebar:
    st.radio("View", ["Dashboard", "Video", "Summary", "Annotate"], key="nav",
             label_visibility="collapsed")
    st.text_input("Your annotator ID", key="annotator_id", placeholder="e.g. HC",
                  help="Set once per session. Logged with every verdict so enrichment "
                       "analysis can detect self-agreement.")
    st.divider()

    st.session_state.setdefault("search_q", "")
    st.text_input("🔎 Search", key="search_q",
                  placeholder="rat name, label, zone, id, note…")
    chips = list(dict.fromkeys(
        load_layout.resolve_subject(s, nm) for s in opts["subjects"] if s != "unknown"))[:5]
    cc = st.columns(2)
    for i, name in enumerate(chips + ["group", "rain_response"]):
        cc[i % 2].button(name, key=f"chip_{name}", use_container_width=True,
                         on_click=_set_query, args=(name,))

    st.markdown("**Filters**")
    span_min = max(1, int((t_max - t_min) / 60000))
    win = st.slider("Time range (min from start)", 0, span_min,
                    (0, min(INITIAL_WINDOW_MIN, span_min)))
    w0, w1 = t_min + win[0] * 60000, t_min + win[1] * 60000

    levels = st.multiselect("Level", opts["levels"], default=opts["levels"])
    subj_sel = st.multiselect("Filter table by subject", opts["subjects"],
                              format_func=lambda s: f"{load_layout.resolve_subject(s, nm)} ({s})")
    label_sel = st.multiselect("Labels", opts["labels"])
    min_bc = st.slider("Min confidence", 0.0, 1.0, 0.0, 0.05)
    with st.expander("More filters"):
        zone_sel = st.multiselect("Zone", opts["zones"])
        stream_sel = st.multiselect("Source stream", opts["source_streams"])
        qc_sel = st.multiselect("QC flag", opts["qc_flags"])
        sm_sel = st.multiselect("State model", opts["state_model_ids"],
                                default=opts["state_model_ids"])
        rain_opt = st.selectbox("Environment", ["any", "rain", "no rain"])
        lens_key = st.selectbox("Lens", ["(none)"] + opts["lens_keys"])
        lens_min = lens_max = None
        include_absent = True
        if lens_key != "(none)":
            lens_min, lens_max = st.slider("Score range", 0.0, 1.0, (0.0, 1.0), 0.05)
            include_absent = st.checkbox("include ABSENT score", value=False)

# --------------------------------------------------------------------------- #
# Search + filter (data layer)
# --------------------------------------------------------------------------- #
q = st.session_state.search_q
base_df = query.text_search(df, q, nm)
fdf = query.filter_episodes(
    base_df, t_start_ms=w0, t_end_ms=w1,
    levels=levels or None, subjects=subj_sel or None, labels=label_sel or None,
    zones=zone_sel or None, source_streams=stream_sel or None,
    state_model_ids=sm_sel or None, qc_flags=qc_sel or None,
    min_boundary_conf=min_bc or None,
    rain=(None if rain_opt == "any" else rain_opt == "rain"),
    lens_key=(None if lens_key == "(none)" else lens_key),
    lens_min=lens_min, lens_max=lens_max, include_absent_lens=include_absent,
)

cov = coverage.compute_coverage(fdf, gaps_df=gaps_df, span=(w0, w1)) if not fdf.empty else {}
n_synth = int(fdf["state_model_id"].map(lambda s: "synth" in str(s)).sum()) if not fdf.empty else 0

with st.sidebar:
    st.markdown(
        f"<div class='infocard'><b>{(w1 - w0) / 60000:.0f} min slice</b><br>"
        f"{day_label(w0)} · {fmt_date(w0)}<br>"
        f"{fmt_ts(w0)}–{fmt_ts(w1)} EDT<br>"
        f"{len(fdf)} episodes · {n_synth} synthetic-cut ⚗️</div>", unsafe_allow_html=True)


def sync_selection(ids: list[str], selected_rows: list[int]) -> str | None:
    if selected_rows:
        st.session_state.sel_id = ids[selected_rows[0]]
    elif st.session_state.sel_id not in ids and ids:
        st.session_state.sel_id = ids[0]
    return st.session_state.sel_id


# =========================================================================== #
# Episode Detail panel (right column / used by multiple views)
# =========================================================================== #
def render_detail(container, sel: str | None):
    with container:
        st.markdown("<div class='card-title'>Episode Detail</div>", unsafe_allow_html=True)
        if sel is None or sel not in df["episode_id"].values:
            st.caption("Select an episode in the table.")
            return
        ep = df[df["episode_id"] == sel].iloc[0].to_dict()
        synth = "synth" in str(ep.get("state_model_id"))
        st.markdown(f"#### {'⚗️ ' if synth else ''}{sel}")
        st.markdown(chips_html(ep.get("labels")), unsafe_allow_html=True)

        ts0 = ep.get("t_start")
        rows = [("Day", f"{day_label(ts0)} · {fmt_date(ts0)}"),
                ("Start", fmt_ts(ts0)), ("End", fmt_ts(ep.get("t_end"))),
                ("Duration", fmt_dur(ep.get("duration_s"))),
                ("Subject", names_of(ep.get("subject_ids"), nm)),
                ("Boundary conf.", ep.get("boundary_confidence")),
                ("Identity conf.", ep.get("identity_confidence")),
                ("Source streams", ", ".join(ep.get("source_streams") or []))]
        wnear = weather.nearest(load_weather_cached(), edt_naive(ts0)) if ts0 is not None else None
        if wnear:
            rows.append(("Weather @ start",
                         f"{wnear.get('temp_c', '—')} °C · {wnear.get('humidity_pct', '—')}% RH · "
                         f"rain {wnear.get('rain_mm_hr', '—')} mm/hr"))
        for k, v in rows:
            st.markdown(f"<div class='kv'><span class='k'>{k}</span>"
                        f"<span class='v'>{'—' if v is None else v}</span></div>",
                        unsafe_allow_html=True)

        st.markdown("**State vector**  <span style='color:#94a3b8;font-size:11px'>"
                    f"(per {ep.get('state_model_id')})</span>", unsafe_allow_html=True)
        sv = ep.get("state_vector") or {}
        g1, g2 = st.columns(2)
        g1.metric("Speed", f"{sv.get('speed', float('nan')):.2f}")
        g2.metric("Group disp.", f"{sv.get('dyad_distance', float('nan')):.1f} in")
        sd = shelter_distance(sv)
        g1.metric("Shelter dist.", "—" if sd is None else f"{sd:.0f} cm")
        act = "high" if sv.get("speed", 0) > 4 else "moderate" if sv.get("speed", 0) > 1.5 else "low"
        g2.metric("Activity", act)

        st.markdown("**Source evidence**")
        streams = ep.get("source_streams") or []
        ev = st.columns(4)
        for i, (name, key) in enumerate([("📡 WISER", "WISER"), ("🎥 Video", "video"),
                                         ("🌡 Thermal", "thermal"), ("📝 Note", "field_note")]):
            present = key in streams
            if key == "video" and present:
                ev[i].button(name, key=f"ev_{sel}_{key}", use_container_width=True,
                             on_click=_go_video, args=(sel,))
            else:
                ev[i].button(name, key=f"ev_{sel}_{key}", use_container_width=True,
                             disabled=not present)

        st.markdown("**Lens scores** <span style='color:#94a3b8;font-size:11px'>"
                    "(UI ranking only; absence ≠ 0)</span>", unsafe_allow_html=True)
        ls = ep.get("lens_scores") if isinstance(ep.get("lens_scores"), dict) else {}
        for k in ("self_surprise", "joint_surprise", "recurrence", "downstream_consequence"):
            v = ls.get(k)
            if v is None:
                st.caption(f"{k}: absent")
            else:
                st.progress(float(v), text=f"{k} · {v:.2f}")

        if ep.get("notes"):
            st.markdown("**Notes**")
            st.info(ep["notes"])

        st.markdown("**Verdict**")
        ann = st.session_state.get("annotator_id", "").strip()
        vb = st.columns(4)
        for i, (lbl, verdict) in enumerate([("⭐ Interesting", "interesting"),
                                            ("？ Unclear", "unclear"),
                                            ("⚠ Artifact", "artifact"),
                                            ("↪ Follow-up", "follow_up")]):
            if vb[i].button(lbl, key=f"vd_{sel}_{verdict}", use_container_width=True):
                if not ann:
                    st.warning("Set your annotator ID in the sidebar first.")
                else:
                    p = annotations.write_annotation(sel, ann, verdict,
                                                     session=st.session_state.session_tag)
                    st.toast(f"Saved '{verdict}' → {p.name}")
        st.caption("Verdicts save as new **append-only** records — nothing is overwritten.")


# =========================================================================== #
# Views
# =========================================================================== #
nav = st.session_state.nav

if nav == "Dashboard":
    with st.expander("How to use", expanded=False):
        st.markdown("**Find** with the sidebar search / filters and the time slider → "
                    "**click a table row** → **inspect** its metadata, evidence, and quality "
                    "caveats in the panel on the right → **record a verdict**. "
                    "Lens scores only *rank*; they are not ground truth. Gaps are shown, not hidden.")

    left, right = st.columns([2.5, 1.2], gap="medium")

    with left:
        with st.container(border=True):
            hc1, hc2 = st.columns([4, 1])
            hc1.markdown("<div class='card-title'>Episodes</div>", unsafe_allow_html=True)
            if not fdf.empty:
                exp = pd.DataFrame({
                    "episode_id": fdf["episode_id"], "level": fdf["level"],
                    "subject": fdf["subject_ids"].map(lambda v: names_of(v, nm)),
                    "start": fdf["t_start"].map(fmt_ts), "label": fdf["labels"].map(top_label),
                })
                hc2.download_button(
                    "⬇ Export CSV", exp.to_csv(index=False), file_name="episodes.csv",
                    use_container_width=True,
                    help="Lossy flat CSV of the visible rows — NOT a re-import path. "
                         "Reload episodes from the Parquet/JSONL store, not this file.")

            # Active-filter + selection strip: loaded / filtered / selected in one line (Q3, Q7).
            _flt = []
            if q:
                _flt.append(f"search “{q}”")
            if levels and set(levels) != set(opts["levels"]):
                _flt.append("level " + "/".join(levels))
            if subj_sel:
                _flt.append("subject " + "/".join(load_layout.resolve_subject(s, nm) for s in subj_sel))
            if label_sel:
                _flt.append("label " + "/".join(label_sel))
            if min_bc > 0:
                _flt.append(f"conf≥{min_bc:.2f}")
            for _name, _val in (("zone", zone_sel), ("source", stream_sel), ("qc", qc_sel)):
                if _val:
                    _flt.append(f"{_name} " + "/".join(_val))
            if sm_sel and set(sm_sel) != set(opts["state_model_ids"]):
                _flt.append("model " + "/".join(sm_sel))
            if rain_opt != "any":
                _flt.append(rain_opt)
            if lens_key != "(none)":
                _flt.append(f"lens {lens_key}")
            _sel = st.session_state.sel_id or "none"
            st.markdown(
                f"<div style='font-size:12px;color:#475569;margin:-2px 0 6px'>"
                f"Showing <b>{len(fdf)}</b> of {len(df)} · {day_label(w0)} "
                f"{fmt_ts(w0)}–{fmt_ts(w1)} EDT · filters: {', '.join(_flt) if _flt else 'none'} · "
                f"selected: <code>{_sel}</code> · <b>click a row to inspect</b></div>",
                unsafe_allow_html=True)

            if fdf.empty:
                st.info("No episodes match the current search / filters / window.")
                ids = []
            else:
                view = pd.DataFrame({
                    "Episode ID": fdf["episode_id"],
                    "Level": fdf["level"],
                    "Subject": fdf["subject_ids"].map(lambda v: names_of(v, nm)),
                    "Day": fdf["t_start"].map(day_label),
                    "Date": fdf["t_start"].map(fmt_date),
                    "Start": fdf["t_start"].map(fmt_ts),
                    "End": fdf["t_end"].map(fmt_ts),
                    "Label": fdf["labels"].map(top_label),
                    "Boundary conf.": fdf["boundary_confidence"],
                    "Lens rank": [lens_rank(r) for r in fdf.to_dict("records")],
                    "Track qual": fdf["tracking_quality"],
                })
                ids = fdf["episode_id"].tolist()
                ev = st.dataframe(
                    view, hide_index=True, use_container_width=True, height=340,
                    on_select="rerun", selection_mode="single-row", key="ep_table",
                    column_config={
                        "Boundary conf.": st.column_config.ProgressColumn(
                            "Boundary conf.", help="Confidence in the entry/exit change-points (0–1).",
                            min_value=0.0, max_value=1.0, format="%.2f"),
                        "Lens rank": st.column_config.ProgressColumn(
                            "Lens rank",
                            help="Max lens score — a UI ranking aid, NOT ground truth. Blank = not scored.",
                            min_value=0.0, max_value=1.0, format="%.2f"),
                        "Track qual": st.column_config.NumberColumn(
                            "Track qual", help="Tracking quality (0–1). Plain number, not a ranking bar.",
                            format="%.2f"),
                    })
                sync_selection(ids, ev.selection.rows)
                st.caption("Bars are UI ranking / quality aids, **not ground truth**. "
                           "Blank *Lens rank* = not scored (never 0).")

        # Focus rats — shared by Field map (trajectory overlay) & Timeline (lane focus).
        name2ids: dict[str, list[str]] = {}
        for sid, nmn in nm.items():
            name2ids.setdefault(nmn, []).append(sid)
        focus_opts = list(dict.fromkeys(
            load_layout.resolve_subject(s, nm) for s in opts["subjects"] if s != "unknown"))
        focus = st.multiselect(
            "🐀 Overlay on map & timeline (focus rats) — draws these rats' tracks and filters the "
            "Timeline lanes; does not filter the table",
            focus_opts, key="focus_rats")
        focus_ids = {i for nmn in focus for i in name2ids.get(nmn, [])}

        # Weather sliced to the window once — reused by the Timeline rain band + Weather panel.
        wdf = load_weather_cached()
        ws = weather.slice_window(wdf, edt_naive(w0), edt_naive(w1)) if not wdf.empty else wdf
        rain_bands = pd.DataFrame()
        if not ws.empty and "rain_mm_hr" in ws.columns:
            rb = ws[ws["rain_mm_hr"] > 0].copy()
            if not rb.empty:
                rb["end"] = rb["ts"] + pd.Timedelta(minutes=5)   # AWN cadence
                rain_bands = rb[["ts", "end", "rain_mm_hr"]]

        import altair as alt
        cA, cB = st.columns([1, 1], gap="small")

        with cA, st.container(border=True):
            st.markdown("<div class='card-title'>Timeline</div>", unsafe_allow_html=True)
            if not fdf.empty:
                tsrc = fdf
                if focus_ids:
                    tsrc = fdf[fdf["subject_ids"].map(
                        lambda v: bool(set(v or []) & focus_ids) if isinstance(v, list) else False)]
                tdf = tsrc.assign(
                    start=tsrc["t_start"].map(edt_naive), end=tsrc["t_end"].map(edt_naive),
                    lane=tsrc["subject_ids"].map(lambda v: names_of(v, nm)),
                    lab=tsrc["labels"].map(top_label))
                bars = alt.Chart(tdf).mark_bar(height=10).encode(
                    x=alt.X("start:T", title="EDT"), x2="end:T",
                    y=alt.Y("lane:N", title=None),
                    color=alt.Color("lab:N", legend=None,
                                    scale=alt.Scale(domain=list(LABEL_COLORS),
                                                    range=list(LABEL_COLORS.values()))),
                    tooltip=["episode_id", "lane", "lab", "start", "end"])
                if not rain_bands.empty:
                    band = alt.Chart(rain_bands).mark_rect(color="#60a5fa", opacity=0.25).encode(
                        x="ts:T", x2="end:T", tooltip=[alt.Tooltip("rain_mm_hr:Q", title="rain mm/hr")])
                    chart = alt.layer(band, bars)
                else:
                    chart = bars
                st.altair_chart(chart.properties(height=200), use_container_width=True)
                cap = []
                if focus:
                    cap.append(f"focused on {', '.join(focus)}")
                if not rain_bands.empty:
                    cap.append("🟦 blue band = rain")
                if cap:
                    st.caption(" · ".join(cap) + ".")

        with cB, st.container(border=True):
            st.markdown("<div class='card-title'>Coverage / QC</div>", unsafe_allow_html=True)
            pct = coverage.overall_completeness(cov)
            if fdf.empty:
                st.caption("—")
            else:
                unk = fdf["subject_ids"].map(
                    lambda v: "unknown" in v if isinstance(v, list) else False).mean() * 100
                lowc = fdf["boundary_confidence"].map(
                    lambda v: v is not None and v < 0.5).mean() * 100
                st.markdown(kpi_html([("Coverage", f"{pct:.0f}%"), ("Gaps", f"{100 - pct:.0f}%"),
                                      ("Unknown ID", f"{unk:.0f}%"), ("Low conf.", f"{lowc:.0f}%")]),
                            unsafe_allow_html=True)
                tot = sum(v["span_s"] for v in cov.values())
                tiled = sum(v["tiled_s"] for v in cov.values())
                st.caption(f"Total {fmt_dur(tot)} · gapped {fmt_dur(tot - tiled)}")

        # Field map — REAL WISER scatter, coloured by a timestamp gradient (native inches).
        with st.container(border=True):
            st.markdown("<div class='card-title'>Field map "
                        "<span style='color:#94a3b8;font-size:11px'>· real WISER positions</span>"
                        "</div>", unsafe_allow_html=True)
            wt_df = real_wiser_window(w0, w1, focus_ids)
            if wt_df.empty:
                av = wiser_tracks.availability()
                st.info(f"No real WISER positions for this window "
                        f"({av['day_files']} day-files in {av['wiser_dir']}). "
                        f"Widen the time range, or set EPISODE_BROWSER_WISER_DIR.")
            else:
                wt_df = wt_df.assign(
                    t=pd.to_datetime(wt_df["ts"], unit="ms", utc=True).dt.tz_convert(EDT_TZ))
                # Colour = rat hue; lightness = time within the window (light early -> dark late).
                hue_map = rat_hue_map(focus_opts)
                span_ms = max(1, w1 - w0)
                tnorm = ((wt_df["ts"] - w0) / span_ms).clip(0, 1)
                wt_df = wt_df.assign(hex=[hex_from_hue(hue_map.get(r, 0.0), tn)
                                         for r, tn in zip(wt_df["rat"], tnorm)])
                lm = wiser_tracks.load_landmarks()
                bnd = wiser_tracks.load_boundary()
                # Fit the view to tracks + landmarks + boundary.
                axs, ays = list(wt_df["x"]), list(wt_df["y"])
                if not lm["rects"].empty:
                    axs += list(lm["rects"]["x0"]) + list(lm["rects"]["x1"])
                    ays += list(lm["rects"]["y0"]) + list(lm["rects"]["y1"])
                if not lm["points"].empty:
                    axs += list(lm["points"]["x"]); ays += list(lm["points"]["y"])
                if bnd:
                    axs += [bnd["x0"], bnd["x1"]]; ays += [bnd["y0"], bnd["y1"]]
                xdom = alt.Scale(domain=[min(axs) - 20, max(axs) + 20], nice=False)
                ydom = alt.Scale(domain=[min(ays) - 20, max(ays) + 20], nice=False)
                encx = alt.X("x:Q", scale=xdom, title="x (WISER inches, offset frame)")
                ency = alt.Y("y:Q", scale=ydom, title="y (WISER inches, offset frame)")

                layers = []
                if bnd:
                    layers.append(alt.Chart(pd.DataFrame([bnd])).mark_rect(
                        fillOpacity=0, stroke="#334155", strokeWidth=1.5, strokeDash=[5, 4]).encode(
                        x=alt.X("x0:Q", scale=xdom), x2="x1:Q",
                        y=alt.Y("y0:Q", scale=ydom), y2="y1:Q"))
                if not lm["rects"].empty:  # shelter / house boxes + tunnel
                    layers.append(alt.Chart(lm["rects"]).mark_rect(
                        fill="#64748b", fillOpacity=0.18, stroke="#475569").encode(
                        x="x0:Q", x2="x1:Q", y="y0:Q", y2="y1:Q", tooltip=["name", "type"]))
                    layers.append(alt.Chart(lm["rects"].assign(
                        cx=(lm["rects"]["x0"] + lm["rects"]["x1"]) / 2)).mark_text(
                        fontSize=10, color="#334155", dy=-2).encode(x="cx:Q", y="y0:Q", text="name:N"))
                if not lm["points"].empty:  # refuges / water / food
                    layers.append(alt.Chart(lm["points"]).mark_point(
                        shape="diamond", size=55, color="#94a3b8", filled=True, opacity=0.8).encode(
                        x="x:Q", y="y:Q", tooltip=["name", "type"]))
                    layers.append(alt.Chart(lm["points"]).mark_text(
                        fontSize=9, color="#64748b", dy=-9).encode(x="x:Q", y="y:Q", text="name:N"))
                # The scatter: colour = per-rat hue, lightness = time (computed per point).
                layers.append(alt.Chart(wt_df).mark_circle(size=55, opacity=0.85).encode(
                    x=encx, y=ency,
                    color=alt.Color("hex:N", scale=None, legend=None),
                    tooltip=["rat", alt.Tooltip("t:T", title="time", format="%H:%M:%S")]))
                st.altair_chart(alt.layer(*layers).properties(height=430), use_container_width=True)

                # One legend: per-rat hue swatch shaded light->dark = earlier->later.
                chips = []
                for r in sorted(wt_df["rat"].unique()):
                    h = hue_map.get(r, 0.0)
                    c0, c1 = hex_from_hue(h, 0.0), hex_from_hue(h, 1.0)
                    chips.append(
                        f"<span style='display:inline-block;width:44px;height:12px;border-radius:3px;"
                        f"vertical-align:middle;background:linear-gradient(90deg,{c0},{c1})'></span>"
                        f" <span style='font-size:12px'>{r}</span>")
                st.markdown("<div style='font-size:12px;color:#64748b'>rat · light = earlier → "
                            "dark = later (EDT):&nbsp;&nbsp;" + "&nbsp;&nbsp;".join(chips) + "</div>",
                            unsafe_allow_html=True)
                st.caption(f"⚠️ Real WISER · **native inches, offset frame — UNVERIFIED vs field cm** "
                           f"(georeference pending) · landmarks (boxes/refuges/water/food) from "
                           f"wiser_rois · {len(wt_df)} points · {wt_df['rat'].nunique()} rats"
                           + (f" · focus {', '.join(focus)}" if focus else ""))

        # Weather — real AWN station data over the window (EDT, unverified alignment).
        with st.container(border=True):
            st.markdown("<div class='card-title'>Weather "
                        "<span style='color:#94a3b8;font-size:11px'>(AWN · EDT · unverified alignment)"
                        "</span></div>", unsafe_allow_html=True)
            if wdf.empty:
                av = weather.availability()
                st.caption(f"No weather data ({av['weather_dir']} — "
                           f"{'not found' if not av['exists'] else 'no AWN CSVs'}). "
                           f"Set EPISODE_BROWSER_WEATHER_DIR to point at the exports.")
            elif ws.empty:
                st.caption("No weather samples inside this time window (widen the range).")
            else:
                import altair as alt
                base = alt.Chart(ws).encode(x=alt.X("ts:T", title="EDT"))
                temp = base.mark_line(color="#dc2626").encode(
                    y=alt.Y("temp_c:Q", title="°C", axis=alt.Axis(titleColor="#dc2626")))
                rain = base.mark_area(color="#2563eb", opacity=0.30).encode(
                    y=alt.Y("rain_mm_hr:Q", title="rain mm/hr", axis=alt.Axis(titleColor="#2563eb")))
                st.altair_chart(alt.layer(rain, temp).resolve_scale(y="independent")
                                .properties(height=150), use_container_width=True)
                tot_rain = float(ws["rain_mm_hr"].fillna(0).mean())
                st.caption(f"temp {ws['temp_c'].min():.0f}–{ws['temp_c'].max():.0f} °C · "
                           f"humidity ~{ws['humidity_pct'].mean():.0f}% · "
                           f"mean rain-rate {tot_rain:.2f} mm/hr · {len(ws)} samples")

    render_detail(right.container(border=True), st.session_state.sel_id)

elif nav == "Video":
    left, right = st.columns([2.5, 1.2], gap="medium")
    with left, st.container(border=True):
        st.markdown("<div class='card-title'>Video preview</div>", unsafe_allow_html=True)
        st.caption("A few **subsampled** frames from an episode's span (no full decode; "
                   "only closed recordings are read).")
        if fdf.empty:
            st.info("No episodes in view — adjust search / filters / window.")
        else:
            ids = fdf["episode_id"].tolist()
            default_ix = ids.index(st.session_state.sel_id) if st.session_state.sel_id in ids else 0
            vid_id = st.selectbox("Episode", ids, index=default_ix, key="video_sel")
            st.session_state.sel_id = vid_id
            ep = df[df["episode_id"] == vid_id].iloc[0].to_dict()
            vid = video_preview.resolve_video(ep, base_dir=HERE)
            if vid is None:
                reason = ("no video source" if "video" not in (ep.get("source_streams") or [])
                          else "no linked_assets.video_path")
                st.info(f"No linked video ({reason}). Pick an episode with a `video` source.")
            else:
                c1, c2, c3 = st.columns([1, 1, 2])
                n_frames = c1.slider("frames", 3, 12, video_preview.DEFAULT_N_FRAMES, key="pv_n")
                width = c2.select_slider("width px", [160, 240, 320, 480], value=320, key="pv_w")
                tag = "⚗️ synthetic clip · " if vid["synthetic"] else ""
                c3.caption(f"{tag}`{vid['path'].name}` @ {vid['start_s']:.1f}–{vid['end_s']:.1f}s")
                if not vid["closed"]:
                    st.warning("Linked file looks OPEN (still recording) — refusing to read it.")
                elif not vid["exists"]:
                    if vid["synthetic"] and str(vid["path"]) == str(video_preview.SAMPLE_CLIP.resolve()):
                        if video_preview.find_ffmpeg() is None:
                            st.info("ffmpeg not found — set EPISODE_BROWSER_FFMPEG to enable previews.")
                        elif st.button("Generate the synthetic sample clip"):
                            made = video_preview.ensure_sample_clip()
                            st.success(f"Created {made.name}") if made else st.error("Could not create clip.")
                            st.rerun()
                    else:
                        st.info(f"Linked video not found: {vid['path']}")
                else:
                    if st.button("Load preview frames", type="primary"):
                        st.session_state.pv_load = vid_id
                    if st.session_state.get("pv_load") == vid_id:
                        frames = cached_frames(str(vid["path"]), vid["start_s"], vid["end_s"],
                                               int(n_frames), int(width), vid["path"].stat().st_mtime)
                        if not frames:
                            st.warning("No frames extracted (ffmpeg missing or seek failed).")
                        else:
                            cols = st.columns(len(frames))
                            for cc, fr in zip(cols, frames):
                                cc.image(fr["png"], caption=f"{fr['t_s']:.1f}s", use_container_width=True)
    render_detail(right.container(border=True), st.session_state.sel_id)

elif nav == "Summary":
    if fdf.empty:
        st.info("Nothing to summarize.")
    else:
        cc1, cc2 = st.columns(2)
        with cc1, st.container(border=True):
            st.markdown("<div class='card-title'>Episodes per subject</div>", unsafe_allow_html=True)
            ex = fdf.explode("subject_ids")
            st.bar_chart(ex["subject_ids"].map(lambda s: load_layout.resolve_subject(s, nm)).value_counts())
            st.markdown("<div class='card-title'>Episodes per level</div>", unsafe_allow_html=True)
            st.bar_chart(fdf["level"].value_counts())
        with cc2, st.container(border=True):
            st.markdown("<div class='card-title'>Lens-score coverage (presence)</div>", unsafe_allow_html=True)
            lens_counts = {k: int(fdf["lens_scores"].map(
                lambda s: isinstance(s, dict) and k in s).sum()) for k in opts["lens_keys"]}
            if lens_counts:
                st.bar_chart(pd.Series(lens_counts))
            st.markdown("<div class='card-title'>QC flag frequency</div>", unsafe_allow_html=True)
            qcc = fdf.explode("qc_flags")["qc_flags"].dropna().value_counts()
            st.bar_chart(qcc) if not qcc.empty else st.write("no QC flags in view")

elif nav == "Annotate":
    st.markdown("### Annotate")
    mode = st.radio("mode", ["Standard", "Blind evaluation"], horizontal=True)
    annotator = st.session_state.get("annotator_id", "").strip()
    st.caption(f"Signed in as **{annotator or 'no annotator ID set'}** (set it in the sidebar). "
               f"Writes append to new timestamped files under outputs/ (session "
               f"`{st.session_state.session_tag}`) — nothing is overwritten.")
    if mode == "Standard":
        sel = st.session_state.sel_id
        if sel is None:
            st.info("Pick an episode in the Dashboard table first.")
        else:
            st.write(f"Annotating **{sel}**")
            verdict = st.radio("verdict", ["interesting", "unclear", "artifact", "follow_up"], horizontal=True)
            add_labels = st.multiselect("add post-hoc labels", opts["labels"])
            note = st.text_area("note", "")
            if st.button("Save annotation"):
                if not annotator:
                    st.warning("Set your annotator ID in the sidebar first.")
                else:
                    p = annotations.write_annotation(sel, annotator, verdict, add_labels, note,
                                                     session=st.session_state.session_tag)
                    st.success(f"Saved → {p.name}")
        recent = annotations.read_log(
            annotations.ANNOT_DIR / f"annotations_{st.session_state.session_tag}.jsonl")
        if recent:
            st.dataframe(pd.DataFrame(recent), use_container_width=True, hide_index=True)
    else:
        st.caption("Judge WITHOUT seeing the score that ranked the episode, then reveal — "
                   "keeps the enrichment showcase from being self-confirming.")
        if not opts["lens_keys"]:
            st.warning("No lens scores to rank by.")
        else:
            rank_lens = st.selectbox("rank by lens", opts["lens_keys"])
            k = st.number_input("top-k", 1, 50, 10)
            ranked = query.rank_by_lens(fdf, rank_lens, drop_absent=True).head(int(k))
            if ranked.empty:
                st.info("No episodes carry that lens in view.")
            else:
                st.session_state.setdefault("blind_i", 0)
                i = min(st.session_state.blind_i, len(ranked) - 1)
                ep = ranked.iloc[i].to_dict()
                st.progress((i + 1) / len(ranked), text=f"episode {i + 1} of {len(ranked)}")
                st.write(f"**{ep['episode_id']}** · {ep['level']} · {names_of(ep['subject_ids'], nm)}")
                st.write({"state_vector": ep.get("state_vector"), "zones": ep.get("zones"),
                          "labels": ep.get("labels"), "notes": ep.get("notes")})
                st.info("🔒 lens_scores hidden — record your verdict first.")
                v = st.radio("verdict", ["interesting", "unclear", "artifact", "follow_up"],
                             horizontal=True, key=f"bv_{ep['episode_id']}")
                a, b = st.columns(2)
                if a.button("Reveal & log verdict"):
                    if not annotator:
                        st.warning("Set your annotator ID in the sidebar first.")
                    else:
                        annotations.log_blind_eval(ep["episode_id"], annotator, rank_lens, v,
                                                   ep.get("lens_scores") or {},
                                                   session=st.session_state.session_tag)
                        st.success(f"Logged. Revealed: {ep.get('lens_scores')}")
                if b.button("Next episode ▶"):
                    st.session_state.blind_i = min(i + 1, len(ranked) - 1)
                    st.rerun()
        evals = annotations.read_log(
            annotations.EVAL_DIR / f"blind_eval_{st.session_state.session_tag}.jsonl")
        if evals:
            st.dataframe(pd.DataFrame(evals), use_container_width=True, hide_index=True)
