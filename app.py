import json
import math
from datetime import date, timedelta

import ee
import folium
import streamlit as st
from streamlit_folium import st_folium

# ------------------------------------------------------------------ SETUP
st.set_page_config(page_title="GeoShield | EO Analytics", page_icon="🛰️", layout="wide")

HOME = "HOME"
ABOUT = "ABOUT"
P1 = "PRODUCT 1 - BORDER FENCING USING SAT IMAGES"
P2 = "PRODUCT 2 - CCS INSTALLATION PREDICTION USING SAT IMAGES"
CONTACT = "CONTACT US"
FEEDBACK = "GIVE FEEDBACK"
PAGES = [HOME, ABOUT, P1, P2, CONTACT, FEEDBACK]

st.session_state.setdefault("page", HOME)


def go(page):
    """Button callback: switches the sidebar menu to another page."""
    st.session_state.page = page


st.markdown(
    """
<style>
html, body, [class*="css"] { font-family: 'Inter', 'Segoe UI', sans-serif; }
.block-container { padding-top: 1.5rem; max-width: 1200px; }
[data-testid="stSidebar"] { background: linear-gradient(180deg,#0b1d3a,#12315e); }
[data-testid="stSidebar"] * { color: #e8f0ff !important; }
.hero { border-radius: 20px; padding: 90px 40px; color: white; margin-bottom: 24px;
  background: linear-gradient(120deg, rgba(8,20,45,.88), rgba(8,20,45,.35)),
  url('https://images.unsplash.com/photo-1446776811953-b23d57bd21aa?auto=format&fit=crop&w=1600&q=70') center/cover; }
.hero h1 { font-size: 3rem; margin: 0 0 10px 0; color: white; }
.hero p { font-size: 1.2rem; max-width: 640px; opacity: .92; }
.card { background: #fff; border-radius: 16px; padding: 24px; border: 1px solid #e3e8f0;
  box-shadow: 0 6px 20px rgba(15,35,70,.08); min-height: 190px; color:#16233b; }
.card h3 { margin-top: 0; color: #12315e; }
.pill { display:inline-block; padding:4px 12px; border-radius:20px; background:#e8f0ff; color:#12315e;
  margin:3px; font-size:.85rem; font-weight:600; }
.dot { display:inline-block; width:12px; height:12px; border-radius:50%; margin:0 6px 0 14px; }
.stButton > button { background: linear-gradient(90deg,#1d6fe8,#14b8a6); color: white; border: 0;
  border-radius: 10px; padding: .6rem 1.4rem; font-weight: 600; }
.stButton > button:hover { filter: brightness(1.1); color: white; }
</style>
""",
    unsafe_allow_html=True,
)


@st.cache_resource(show_spinner=False)
def init_ee():
    """Connects to Google Earth Engine using the service account in Streamlit secrets."""
    try:
        info = dict(st.secrets["gee_service_account"])
        creds = ee.ServiceAccountCredentials(info["client_email"], key_data=json.dumps(info))
        ee.Initialize(creds, project=info["project_id"])
        return None
    except Exception as e:  # noqa
        return str(e)


def need_ee():
    err = init_ee()
    if err:
        st.error("Google Earth Engine is not connected yet. Complete the 'Earth Engine setup' "
                 "step in the guide and add your secret in Streamlit settings.")
        st.code(err)
        st.stop()


ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"


def base_map(center, zoom):
    m = folium.Map(location=center, zoom_start=zoom, tiles=None)
    folium.TileLayer(ESRI, attr="Esri World Imagery", name="Satellite").add_to(m)
    folium.TileLayer("OpenStreetMap", name="Street map", show=False).add_to(m)
    return m


def legend(items):
    html = "".join(f'<span class="dot" style="background:{c}"></span>{t}' for c, t in items)
    st.markdown(html, unsafe_allow_html=True)


# ------------------------------------------------------- PRODUCT 1 LOGIC
BORDER_SECTORS = {
    "Sylhet - Meghalaya (hills & rivers)": (91.5, 24.8, 92.4, 25.3),
    "Tripura - Comilla (mixed terrain)": (91.2, 23.0, 91.9, 24.0),
    "Sundarbans - West Bengal (delta/mangrove)": (88.5, 21.6, 89.3, 22.5),
    "Dinajpur plains (open farmland)": (88.2, 25.2, 89.0, 26.2),
}


@st.cache_data(ttl=21600, show_spinner=False)
def border_layer(bbox, slope_max, ndvi_max, width_km, year):
    aoi = ee.Geometry.Rectangle(list(bbox))
    lsib = ee.FeatureCollection("USDOS/LSIB_SIMPLE/2017")
    bd = lsib.filter(ee.Filter.eq("country_na", "Bangladesh")).geometry()
    ind = lsib.filter(ee.Filter.eq("country_na", "India")).geometry()
    w = width_km * 1000
    # thin strip inside Bangladesh that touches India = the border corridor
    strip = (bd.difference(bd.buffer(-w, 500), 500)
             .intersection(ind.buffer(w, 500), 500)
             .intersection(aoi, 500))

    def s2(a, b):
        return (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(aoi)
                .filterDate(a, b).filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 30)).median())

    ndvi = s2(f"{year}-01-01", f"{year}-03-31").normalizedDifference(["B8", "B4"])
    ndwi = s2(f"{year}-06-01", f"{year}-09-30").normalizedDifference(["B3", "B8"])
    vv = (ee.ImageCollection("COPERNICUS/S1_GRD").filterBounds(aoi)
          .filterDate(f"{year}-06-01", f"{year}-09-30")
          .filter(ee.Filter.eq("instrumentMode", "IW"))
          .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
          .select("VV").median())
    water = ndwi.gt(0.1).Or(vv.lt(-18)).unmask(0).focal_max(150, "circle", "meters")
    slope = ee.Terrain.slope(ee.Image("USGS/SRTMGL1_003"))

    cls = (ee.Image(1).where(ndvi.gt(ndvi_max).unmask(0), 2)
           .where(slope.gt(slope_max).Or(water), 3).rename("cls").clip(strip))
    vis = {"min": 1, "max": 3, "palette": ["#22c55e", "#facc15", "#ef4444"]}
    url = cls.getMapId(vis)["tile_fetcher"].url_format
    hist = cls.reduceRegion(ee.Reducer.frequencyHistogram(), strip, 150,
                            maxPixels=1e9, bestEffort=True).getInfo().get("cls", {})
    return url, {k: float(v) for k, v in hist.items()}


def page_product1():
    st.title("🛡️ Product 1 – Border Fencing Feasibility")
    st.caption("Case study: India–Bangladesh border. Terrain (SRTM), vegetation (Sentinel-2 NDVI) "
               "and monsoon water (Sentinel-2 NDWI + Sentinel-1 radar) decide where fences can be built.")
    need_ee()
    c1, c2, c3, c4 = st.columns(4)
    sector = c1.selectbox("Border sector", list(BORDER_SECTORS))
    slope_max = c2.slider("Max slope (°)", 10, 45, 25)
    ndvi_max = c3.slider("Dense-jungle NDVI above", 0.3, 0.9, 0.6, 0.05)
    year = c4.selectbox("Imagery year", [2024, 2023, 2022, 2021])
    width = st.slider("Corridor width (km)", 1, 6, 3)

    bbox = BORDER_SECTORS[sector]
    with st.spinner("Crunching satellite data on Google Earth Engine (30-90 s the first time)..."):
        url, hist = border_layer(bbox, slope_max, ndvi_max, width, year)

    center = [(bbox[1] + bbox[3]) / 2, (bbox[0] + bbox[2]) / 2]
    m = base_map(center, 9)
    folium.TileLayer(url, attr="Google Earth Engine", name="Fencing feasibility", overlay=True).add_to(m)
    folium.LayerControl().add_to(m)
    legend([("#22c55e", "Feasible"), ("#facc15", "High maintenance / jungle"),
            ("#ef4444", f"Infeasible: river or slope > {slope_max}°")])
    st_folium(m, height=560, use_container_width=True, returned_objects=[])

    total = sum(hist.values()) or 1
    a, b, c = st.columns(3)
    a.metric("🟢 Feasible", f"{100 * hist.get('1', 0) / total:.0f}%")
    b.metric("🟡 High maintenance", f"{100 * hist.get('2', 0) / total:.0f}%")
    c.metric("🔴 Infeasible (smart surveillance)", f"{100 * hist.get('3', 0) / total:.0f}%")
    st.info("Prototype note: the border corridor is drawn from the LSIB boundary dataset, so lines are "
            "approximate. Results are for demonstration, not for operational planning.")


# ------------------------------------------------------- PRODUCT 2 LOGIC
CCS_REGIONS = {
    "Gulf of Mexico – Houston to Louisiana": (-95.8, 29.0, -90.5, 30.3),
    "Middle East – Jubail / Dammam": (49.2, 26.0, 50.4, 27.2),
    "Middle East – Abu Dhabi / Ruwais": (52.4, 24.0, 54.0, 24.8),
}


def km_between(a, b):
    r = 6371
    dlat, dlon = math.radians(b[1] - a[1]), math.radians(b[0] - a[0])
    h = (math.sin(dlat / 2) ** 2 + math.cos(math.radians(a[1])) * math.cos(math.radians(b[1]))
         * math.sin(dlon / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(h))


@st.cache_data(ttl=21600, show_spinner=False)
def ccs_layer(bbox, start, end, w_e, w_t, w_h, min_emis):
    aoi = ee.Geometry.Rectangle(list(bbox))
    no2 = (ee.ImageCollection("COPERNICUS/S5P/OFFL/L3_NO2")
           .select("tropospheric_NO2_column_number_density")
           .filterDate(start, end).filterBounds(aoi).mean())
    era = (ee.ImageCollection("ECMWF/ERA5_LAND/MONTHLY_AGGR")
           .filterDate(start, end).select(["temperature_2m", "dewpoint_temperature_2m"]).mean())
    t = era.select("temperature_2m").subtract(273.15)
    td = era.select("dewpoint_temperature_2m").subtract(273.15)

    def mag(x):
        return x.multiply(17.625).divide(x.add(243.04))

    rh = mag(td).exp().divide(mag(t).exp()).multiply(100).clamp(0, 100)

    emis = no2.subtract(2e-5).divide(1.3e-4).clamp(0, 1)      # emission intensity 0-1
    tsc = ee.Image(45).subtract(t).divide(30).clamp(0, 1)      # cooler air = better absorption
    hsc = rh.divide(80).clamp(0, 1)                            # humid air = lower solvent loss
    tot = w_e + w_t + w_h
    idx = (emis.multiply(w_e / tot).add(tsc.multiply(w_t / tot)).add(hsc.multiply(w_h / tot))
           .multiply(100).rename("idx").updateMask(emis.gt(min_emis)).clip(aoi))

    vis = {"min": 0, "max": 100, "palette": ["#2c7bb6", "#abd9e9", "#ffffbf", "#fdae61", "#d7191c"]}
    url = idx.getMapId(vis)["tile_fetcher"].url_format

    pts = (idx.addBands(no2.rename("no2")).addBands(t.rename("t")).addBands(rh.rename("rh"))
           .sample(region=aoi, scale=3000, geometries=True)
           .sort("idx", False).limit(200).getInfo()["features"])
    picked = []
    for f in pts:  # keep top sites at least 25 km apart
        xy = f["geometry"]["coordinates"]
        if all(km_between(xy, p["xy"]) > 25 for p in picked):
            p = f["properties"]
            picked.append({"xy": xy, "idx": p["idx"], "no2": p["no2"], "t": p["t"], "rh": p["rh"]})
        if len(picked) == 5:
            break
    return url, picked


def page_product2():
    st.title("🏭 Product 2 – CCS Installation Predictor")
    st.caption("Sentinel-5P NO₂ marks industrial emission hotspots (a proxy for CO₂ sources); ERA5-Land "
               "temperature and humidity estimate how well amine capture would perform there.")
    need_ee()
    c1, c2 = st.columns([2, 2])
    region = c1.selectbox("Industrial corridor", list(CCS_REGIONS))
    dates = c2.date_input("Analysis period", (date.today() - timedelta(days=365), date.today() - timedelta(days=10)))
    w1, w2, w3, w4 = st.columns(4)
    w_e = w1.slider("Weight: emissions", 0.0, 1.0, 0.5, 0.05)
    w_t = w2.slider("Weight: temperature", 0.0, 1.0, 0.3, 0.05)
    w_h = w3.slider("Weight: humidity", 0.0, 1.0, 0.2, 0.05)
    min_emis = w4.slider("Min emission level", 0.05, 0.6, 0.15, 0.05)
    if len(dates) != 2 or (w_e + w_t + w_h) == 0:
        st.warning("Pick a start and end date, and keep at least one weight above 0.")
        st.stop()

    bbox = CCS_REGIONS[region]
    with st.spinner("Processing Sentinel-5P and ERA5 data on Google Earth Engine..."):
        url, picked = ccs_layer(bbox, str(dates[0]), str(dates[1]), w_e, w_t, w_h, min_emis)

    center = [(bbox[1] + bbox[3]) / 2, (bbox[0] + bbox[2]) / 2]
    m = base_map(center, 8)
    folium.TileLayer(url, attr="Google Earth Engine", name="CCS Efficiency Index", overlay=True,
                     opacity=0.8).add_to(m)
    for i, p in enumerate(picked, 1):
        folium.Marker([p["xy"][1], p["xy"][0]], tooltip=f"Candidate #{i}: index {p['idx']:.0f}",
                      popup=f"NO₂: {p['no2'] * 1e6:.0f} µmol/m² | Temp: {p['t']:.1f} °C | RH: {p['rh']:.0f}%",
                      icon=folium.Icon(color="darkred", icon="star")).add_to(m)
    folium.LayerControl().add_to(m)
    legend([("#2c7bb6", "Low efficiency index"), ("#ffffbf", "Medium"), ("#d7191c", "High efficiency index")])
    st_folium(m, height=560, use_container_width=True, returned_objects=[])

    st.subheader("Top candidate installation sites")
    if picked:
        st.dataframe([{"Rank": i, "Latitude": round(p["xy"][1], 3), "Longitude": round(p["xy"][0], 3),
                       "Efficiency index": round(p["idx"], 1), "NO₂ (µmol/m²)": round(p["no2"] * 1e6, 1),
                       "Temp (°C)": round(p["t"], 1), "Rel. humidity (%)": round(p["rh"])}
                      for i, p in enumerate(picked, 1)], use_container_width=True, hide_index=True)
    else:
        st.write("No hotspots found. Lower the 'Min emission level' slider.")
    st.info("The index is a simplified screening heuristic (not an engineering design). NO₂ is a proxy "
            "for combustion-related CO₂ sources.")


# ------------------------------------------------------------ OTHER PAGES
def page_home():
    st.markdown(
        """<div class="hero"><h1>See the planet. Decide with confidence.</h1>
        <p>GeoShield turns free satellite data into decision-ready intelligence for border security
        and industrial decarbonisation.</p></div>""",
        unsafe_allow_html=True,
    )
    a, b = st.columns(2)
    a.markdown("""<div class="card"><h3>🛡️ Product 1 – Border Fencing</h3>
    Predicts where physical fencing is feasible along the India–Bangladesh border, and where rivers,
    steep slopes or jungle call for smart surveillance instead.</div>""", unsafe_allow_html=True)
    b.markdown("""<div class="card"><h3>🏭 Product 2 – CCS Installation</h3>
    Finds industrial emission hotspots in the Gulf of Mexico and Middle East and scores how well
    amine carbon capture would perform given local weather.</div>""", unsafe_allow_html=True)
    st.write("")
    x, y, _ = st.columns([1, 1, 3])
    x.button("Use Product 1", on_click=go, args=(P1,), key="cta1")
    y.button("Use Product 2", on_click=go, args=(P2,), key="cta2")
    st.write("")
    st.image("https://images.unsplash.com/photo-1451187580459-43490279c0fa?auto=format&fit=crop&w=1400&q=70",
             use_container_width=True)


def page_about():
    st.title("About GeoShield")
    st.write("**Mission:** make advanced Earth Observation analytics accessible, so Defense and Energy "
             "teams can screen locations in minutes instead of months, using open satellite data.")
    st.subheader("Technology stack")
    for t in ["Google Earth Engine", "Sentinel-1 radar", "Sentinel-2 optical", "Sentinel-5P TROPOMI",
              "SRTM DEM", "ERA5-Land climate", "Streamlit", "Folium", "Python"]:
        st.markdown(f'<span class="pill">{t}</span>', unsafe_allow_html=True)
    st.subheader("Target sectors")
    a, b = st.columns(2)
    a.markdown("""<div class="card"><h3>Defense & Border Security</h3>Terrain, vegetation and flood
    analysis to plan fencing versus sensor-based surveillance.</div>""", unsafe_allow_html=True)
    b.markdown("""<div class="card"><h3>Energy & Climate</h3>Emission hotspot mapping and weather-based
    screening for carbon capture and storage (CCS) projects.</div>""", unsafe_allow_html=True)
    st.caption("This is a student prototype. Outputs are indicative and need ground validation.")


def page_contact():
    st.title("Contact Us")
    left, right = st.columns(2)
    left.markdown("""<div class="card"><h3>Get in touch</h3>
    📧 hello@geoshield.example<br>📞 +91 00000 00000<br>📍 Hyderabad, Telangana, India<br>
    🕘 Mon–Fri, 9:00–17:00 IST</div>""", unsafe_allow_html=True)
    with right.form("contact"):
        name = st.text_input("Name")
        email = st.text_input("Email")
        msg = st.text_area("Message")
        if st.form_submit_button("Send message"):
            if name and email and msg:
                st.success(f"Thanks {name}! (Sample form: the message is not actually sent.)")
            else:
                st.warning("Please fill in all fields.")


def page_feedback():
    st.title("Give Feedback")
    st.write("How useful was the platform?")
    rating = st.feedback("stars")
    product = st.selectbox("Which product did you try?", ["Product 1", "Product 2", "Both"])
    text = st.text_area("Your comments")
    if st.button("Submit feedback"):
        if rating is None:
            st.warning("Please choose a star rating first.")
        else:
            st.success(f"Thank you! You rated {product}: {rating + 1}/5.")
            st.balloons()


# ------------------------------------------------------------- NAVIGATION
with st.sidebar:
    st.markdown("## 🛰️ GeoShield")
    st.radio("Navigate", PAGES, key="page", label_visibility="collapsed")

{HOME: page_home, ABOUT: page_about, P1: page_product1, P2: page_product2,
 CONTACT: page_contact, FEEDBACK: page_feedback}[st.session_state.page]()
