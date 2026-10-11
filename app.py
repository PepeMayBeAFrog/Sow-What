import sys
import json
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from datetime import date
from dataclasses import dataclass


def climate_for_place(place):
    """Fetch average temperature (°C) and annual rainfall (mm) for a place."""
    geo = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": place, "count": 1, "language": "en", "format": "json"},
        timeout=20,
    )
    geo.raise_for_status()
    locations = geo.json().get("results", [])
    if not locations:
        raise ValueError(f"Could not find location: {place}")

    location = locations[0]
    lat, lon = location["latitude"], location["longitude"]

    end_year = date.today().year -1
    start_year = end_year - 1

    weather = requests.get(
        "https://archive-api.open-meteo.com/v1/archive",
        params={
            "latitude": lat,
            "longitude": lon,
            "start_date": f"{start_year}-01-01",
            "end_date": f"{end_year}-12-31",
            "daily": "temperature_2m_mean,precipitation_sum",
            "timezone": "auto",
        },
        timeout=30,
    )
    weather.raise_for_status()
    daily = weather.json()["daily"]

    temps = [x for x in daily["temperature_2m_mean"] if x is not None]
    rain = [x for x in daily["precipitation_sum"] if x is not None]

    return {
        "matched_location": f'{location["name"]}, {location.get("country", "")}',
        "latitude": lat,
        "longitude": lon,
        "period": f"{start_year}-{end_year}",
        "avg_daily_temp_c": sum(temps) / len(temps) if temps else None,
        "avg_annual_rainfall_mm": sum(rain) / (end_year - start_year + 1) if rain else None,
    }


WEIGHTS = {
    "pH": 0.25,
    "Temperature": 0.25,
    "Rainfall": 0.20,
    "Sustainability": 0.30,
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9, "Weights must sum to 1"


@dataclass
class Crop:
    name: str
    category: str
    ph: tuple
    temp: tuple
    rainfall: tuple
    soil_health: int
    season: str
    sowing: str
    duration: str


CROPS = [
    Crop("Rice", "Food", (5.0, 5.5, 6.5, 8.5), (10, 20, 35, 42),
         (800, 1000, 2000, 3000), 8,
         "Kharif", "June - July", "120 - 150 days"),
    Crop("Wheat", "Food", (5.5, 6.5, 7.5, 8.5), (3.5, 10, 25, 35),
         (250, 500, 750, 1000), 29,
         "Rabi", "October - November", "110 - 130 days"),
    Crop("Maize", "Food", (4.5, 5.5, 7.5, 8.5), (8, 18, 32, 42),
         (400, 500, 1000, 1500), 26,
         "Kharif / Rabi", "June - July (Kharif)", "90 - 110 days"),
    Crop("Pearl Millet (Bajra)", "Food", (4.0, 5.5, 7.0, 8.5), (10, 20, 35, 45),
         (200, 350, 600, 900), 53,
         "Kharif", "June - July", "70 - 90 days"),
    Crop("Finger Millet (Ragi)", "Food", (4.5, 5.5, 7.0, 8.0), (8, 20, 32, 38),
         (300, 500, 1000, 1500), 44,
         "Kharif", "June - July", "100 - 130 days"),
    Crop("Green Gram (Moong)", "Food", (5.5, 6.3, 7.5, 8.5), (10, 25, 35, 45),
         (300, 600, 900, 1200), 80,
         "Kharif / Summer", "March - April or June - July", "60 - 70 days"),
    Crop("Chickpea", "Food", (5.5, 6.0, 8.0, 8.6), (5, 15, 30, 38),
         (400, 600, 900, 1200), 72,
         "Rabi", "October - November", "95 - 120 days"),
    Crop("Cotton", "Cash", (5.2, 6.0, 7.5, 8.5), (12, 21, 35, 45),
         (500, 700, 1200, 1600), 16,
         "Kharif", "May - June", "150 - 180 days"),
    Crop("Sugarcane", "Cash", (5.0, 6.5, 7.5, 8.5), (10, 20, 35, 45),
         (600, 1000, 1500, 2500), 8,
         "Annual", "February - March", "10 - 14 months"),
    Crop("Groundnut", "Cash", (5.0, 5.5, 7.0, 8.0), (12, 22, 32, 40),
         (400, 500, 1000, 1400), 72,
         "Kharif", "June - July", "100 - 130 days"),
]

# Sustainability Rubrics breakdown based on scientific multi-factor model:
# Composite = 0.40 * N_fix + 0.20 * Poor_soil + 0.25 * Duration + 0.15 * GHG
SUSTAINABILITY_RUBRICS = {
    "Rice": {
        "n_fix": 0, "poor_soil": 0, "duration": 33, "ghg": 0, "score": 8,
        "n_fix_label": "None (non-legume)",
        "poor_soil_label": "Low (demands fertile, puddled soil)",
        "duration_label": "Long cycle (120-150 d)",
        "ghg_label": "High methane (flooded paddy)",
    },
    "Wheat": {
        "n_fix": 0, "poor_soil": 0, "duration": 67, "ghg": 82, "score": 29,
        "n_fix_label": "None (non-legume)",
        "poor_soil_label": "Moderate",
        "duration_label": "Medium cycle (110-130 d)",
        "ghg_label": "Low-medium emissions",
    },
    "Maize": {
        "n_fix": 0, "poor_soil": 0, "duration": 67, "ghg": 63, "score": 26,
        "n_fix_label": "None (heavy nutrient feeder)",
        "poor_soil_label": "Low-moderate",
        "duration_label": "Medium cycle (90-110 d)",
        "ghg_label": "Moderate synthetic N demand",
    },
    "Pearl Millet (Bajra)": {
        "n_fix": 0, "poor_soil": 100, "duration": 100, "ghg": 50, "score": 53,
        "n_fix_label": "None",
        "poor_soil_label": "Exceptional (drought & sandy soil hardy)",
        "duration_label": "Short cycle (70-90 d, soil rest)",
        "ghg_label": "Low input footprint",
    },
    "Finger Millet (Ragi)": {
        "n_fix": 0, "poor_soil": 100, "duration": 67, "ghg": 50, "score": 44,
        "n_fix_label": "None",
        "poor_soil_label": "High (thrives in degraded red soils)",
        "duration_label": "Medium cycle (100-130 d)",
        "ghg_label": "Low input footprint",
    },
    "Green Gram (Moong)": {
        "n_fix": 100, "poor_soil": 0, "duration": 100, "ghg": 100, "score": 80,
        "n_fix_label": "High biological N-fixation",
        "poor_soil_label": "Requires well-drained loam",
        "duration_label": "Ultra-short (60-70 d, soil enrichment)",
        "ghg_label": "Net carbon positive (zero N-fertilizer)",
    },
    "Chickpea": {
        "n_fix": 100, "poor_soil": 0, "duration": 67, "ghg": 100, "score": 72,
        "n_fix_label": "High biological N-fixation",
        "poor_soil_label": "Moderate",
        "duration_label": "Medium cycle (95-120 d)",
        "ghg_label": "Very low carbon footprint",
    },
    "Cotton": {
        "n_fix": 0, "poor_soil": 0, "duration": 33, "ghg": 50, "score": 16,
        "n_fix_label": "None (chemical input intensive)",
        "poor_soil_label": "Low (salinity sensitive)",
        "duration_label": "Long cycle (150-180 d)",
        "ghg_label": "High pesticide/energy load",
    },
    "Sugarcane": {
        "n_fix": 0, "poor_soil": 0, "duration": 0, "ghg": 50, "score": 8,
        "n_fix_label": "None (exhaustive nutrient extractor)",
        "poor_soil_label": "Demands deep rich soil & water",
        "duration_label": "Annual (10-14 mo, continuous soil depletion)",
        "ghg_label": "High irrigation & transport energy",
    },
    "Groundnut": {
        "n_fix": 100, "poor_soil": 0, "duration": 67, "ghg": 100, "score": 72,
        "n_fix_label": "High biological N-fixation",
        "poor_soil_label": "Requires loose sandy soil",
        "duration_label": "Medium cycle (100-130 d)",
        "ghg_label": "Low GHG impact",
    },
}

# Post-harvest storage recommendations for all crops (ICAR / FAO / ICRISAT guidelines)
CROP_STORAGE = {
    "Rice": {
        "shelf_life": "12 to 24 months (stored as unhusked rough paddy)",
        "moisture": "Dry to 13% – 14% moisture content",
        "method": "Store as rough paddy with protective husk intact in airtight hermetic bags (IRRI SuperBag) or galvanized metal silos raised off floor on pallets. Protect against rice weevil (Sitophilus oryzae) and moisture condensation.",
    },
    "Wheat": {
        "shelf_life": "12 to 18 months",
        "moisture": "Sun-dry to below 11% – 12% moisture",
        "method": "Store in airtight galvanized iron drums or Pusa bins in a clean, elevated, well-ventilated shed. Add dry neem leaves or food-grade diatomaceous earth to deter Khapra beetle (Trogoderma granarium) and lesser grain borer.",
    },
    "Maize": {
        "shelf_life": "6 to 12 months",
        "moisture": "Thoroughly dry to below 12% – 13% moisture",
        "method": "Ensure complete kernel drying before bagging to strictly prevent Aspergillus flavus mold and toxic aflatoxins. Store shelled grain in hermetic silos or polythene-lined woven bags on wooden slats with rat-guards.",
    },
    "Pearl Millet (Bajra)": {
        "shelf_life": "6 to 12 months (whole grain only)",
        "moisture": "Dry to 10% – 11% moisture",
        "method": "Store strictly as whole unground grain in sealed tin containers or moisture-proof polythene bags. Whole grains keep well, but milled bajra flour develops rancidity within 10–15 days due to lipase oxidation.",
    },
    "Finger Millet (Ragi)": {
        "shelf_life": "24 to 36+ months (one of the longest-storing grains)",
        "moisture": "Sun-dry to below 12% moisture",
        "method": "Store in clean metal drums, mud bins (kothar), or hermetic sacks kept in dark, cool spots. Ragi's high seed-coat tannin and small seed size naturally resist storage pests and weevil penetration.",
    },
    "Green Gram (Moong)": {
        "shelf_life": "8 to 12 months",
        "moisture": "Dry to 9% – 10% moisture",
        "method": "Store in airtight containers or hermetic bags. Coat dry seed with edible vegetable/castor oil (5 ml per kg seed) or mix with inert neem leaf powder to form a physical barrier against pulse beetle (Callosobruchus chinensis).",
    },
    "Chickpea": {
        "shelf_life": "8 to 12 months",
        "moisture": "Dry to 9% – 10% moisture",
        "method": "Store in airtight metal drums or thick moisture-barrier sacks with moisture desiccants. Pulse bruchids multiply rapidly in warm humid air; fumigate or mix sun-dried neem leaves, and keep storage containers tightly sealed.",
    },
    "Cotton": {
        "shelf_life": "3 to 6 months (seed cotton / lint)",
        "moisture": "Ensure moisture remains below 8% – 9%",
        "method": "Keep seed cotton in elevated, well-ventilated warehouses with concrete flooring. Press into standard density bales wrapped in hessian. Keep strictly dry and free of oil/sparks, as damp cotton discolors and self-heats.",
    },
    "Sugarcane": {
        "shelf_life": "Highly perishable (crush within 24–48 hours of harvest)",
        "moisture": "Fresh harvested stalks (no long storage possible)",
        "method": "Transport directly to the sugar mill immediately after cutting. Every 24 hours of delay reduces sucrose recovery and increases invert sugars. If transport is delayed, stack in shade and cover with moist cane trash.",
    },
    "Groundnut": {
        "shelf_life": "6 to 9 months (as unshelled pods)",
        "moisture": "Dry pods to 7% – 8% moisture",
        "method": "Store strictly in uncracked pod shells inside breathable jute/gunny bags stacked on wooden dunnage in cool, dry godowns. Avoid polythene bags because trapped humidity causes Aspergillus fungi and deadly aflatoxin contamination.",
    },
}


def range_score(value, limits):
    """1.0 inside the optimum range, falling to 0.0 at the absolute limits."""
    abs_min, opt_min, opt_max, abs_max = limits
    if value < abs_min or value > abs_max:
        return 0.0
    if opt_min <= value <= opt_max:
        return 1.0
    if value < opt_min:
        return (value - abs_min) / (opt_min - abs_min)
    return (abs_max - value) / (abs_max - opt_max)


def label_for(score):
    if score >= 75:
        return "Highly suitable"
    if score >= 55:
        return "Suitable"
    if score >= 40:
        return "Marginal"
    return "Not recommended"


def evaluate(crop, ph, temp, rainfall):
    """Score one crop and return a result dictionary."""
    parts = {
        "pH": range_score(ph, crop.ph),
        "Temperature": range_score(temp, crop.temp),
        "Rainfall": range_score(rainfall, crop.rainfall),
        "Sustainability": crop.soil_health / 100,
    }
    total = round(100 * sum(WEIGHTS[k] * v for k, v in parts.items()), 1)

    reasons = []
    if parts["pH"] == 0:
        reasons.append(f"Soil pH {ph} is outside tolerable range ({crop.ph[0]} - {crop.ph[3]})")
    if parts["Temperature"] == 0:
        reasons.append(f"Temperature {temp:.1f} °C is outside tolerable range ({crop.temp[0]} - {crop.temp[3]} °C)")
    if parts["Rainfall"] == 0:
        reasons.append(f"Rainfall {rainfall:.0f} mm is outside tolerable range ({crop.rainfall[0]} - {crop.rainfall[3]} mm)")

    return {
        "crop": crop,
        "score": total,
        "parts": parts,
        "label": label_for(total),
        "reasons": reasons,
    }


def recommend(ph, temp, rainfall):
    results = [evaluate(c, ph, temp, rainfall) for c in CROPS]
    suitable = sorted((r for r in results if not r["reasons"]), key=lambda r: r["score"], reverse=True)
    unsuitable = [r for r in results if r["reasons"]]
    return suitable, unsuitable


def escape_html(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def render_html_page(place_input="Vellore", ph_input="6.5", error_msg="", results_payload=None):
    styles = """
:root{--bg:#fff;--ink:#000;--mute:#e6e6e6;
box-sizing:border-box;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#000;--ink:#fff;--mute:#222}}
html{scroll-padding-top:env(safe-area-inset-top,0px)}
*{box-sizing:border-box;border-radius:0}
body{margin:0;background:var(--bg);color:var(--ink);font-family:"Courier New",Courier,monospace;line-height:1.5;font-size:16px}
.wrap{max-width:960px;margin:0 auto;padding:24px 16px 64px}
h1{font-family:Helvetica,Arial,sans-serif;font-weight:900;font-size:clamp(2.4rem,9vw,5.5rem);line-height:.9;letter-spacing:-.04em;margin:0 0 16px}
.sub{max-width:60ch;margin:0 0 32px}
.grid{display:grid;grid-template-columns:320px 1fr;gap:32px;align-items:start;border-top:3px solid var(--ink);padding-top:24px}
@media(max-width:820px){.grid{grid-template-columns:1fr}}
.lab{display:block;font-weight:700;margin:20px 0 6px}
.lab:first-child{margin-top:0}
input[type=text],input[type=number]{width:100%;font:inherit;padding:8px;border:2px solid var(--ink);background:var(--bg);color:var(--ink)}
input:focus,button:focus-visible{outline:3px solid var(--ink);outline-offset:3px}
.go{width:100%;margin-top:28px;font:inherit;font-weight:700;padding:12px;background:var(--ink);color:var(--bg);border:2px solid var(--ink);cursor:pointer}
.go:hover{background:var(--bg);color:var(--ink)}
.err{border:2px solid var(--ink);padding:6px 10px;margin-top:12px;font-weight:700}
.empty{border:2px solid var(--ink);padding:16px}
.ctx{border:2px solid var(--ink);padding:8px 12px;margin-bottom:24px;font-weight:700}
.climate-box{border:2px solid var(--ink);padding:12px;margin-top:16px}
.climate-box .row{display:flex;justify-content:space-between;padding:2px 0}
.climate-box .val{font-weight:700}
.card{border:2px solid var(--ink);padding:16px;margin-bottom:-2px}
.card.first{border-width:5px;margin-bottom:12px}
.top{display:flex;gap:12px;justify-content:space-between;flex-wrap:wrap;align-items:flex-start}
.rank{font-weight:700}
.name{font-family:Helvetica,Arial,sans-serif;font-weight:900;font-size:1.8rem;letter-spacing:-.02em;line-height:1.1}
.score{font-family:Helvetica,Arial,sans-serif;font-weight:900;font-size:2.6rem;line-height:1}
.score small{font-size:1rem;font-weight:400;font-family:"Courier New",monospace}
.meta{margin-top:4px}
.bars{margin-top:14px;display:grid;gap:4px}
.bar{display:grid;grid-template-columns:120px 1fr 44px;gap:8px;align-items:center;font-size:.9rem}
.track{height:12px;border:2px solid var(--ink)}
.fill{height:100%;background:var(--ink)}
.cycle{margin-top:14px;border-top:2px solid var(--ink);padding-top:10px}
h2{font-family:Helvetica,Arial,sans-serif;font-weight:900;margin:32px 0 8px;font-size:1.2rem}
.small{display:flex;justify-content:space-between;gap:8px;padding:6px 0;border-bottom:1px solid var(--ink)}
.no{border-left:5px solid var(--ink);padding:4px 10px;margin-bottom:8px}
.foot{margin-top:40px;font-size:.85rem;max-width:70ch;border-top:1px solid var(--ink);padding-top:12px}

/* Expandable Other Options Dropdown */
.dropdown-menu{border:2px solid var(--ink);margin-top:10px;background:var(--bg)}
.dropdown-toggle{padding:12px 16px;font-weight:900;display:flex;justify-content:space-between;align-items:center;background:var(--bg);cursor:pointer;user-select:none;border-bottom:2px solid transparent}
.dropdown-toggle:hover{background:var(--mute)}
.dropdown-arrow{transition:transform .2s ease;font-size:1.1rem}
.dropdown-content{display:none;padding:12px;background:var(--bg)}
.dropdown-menu:hover .dropdown-content, .dropdown-menu:focus-within .dropdown-content{display:block}
.dropdown-menu:hover .dropdown-toggle{background:var(--mute);border-bottom-color:var(--ink)}
.dropdown-menu:hover .dropdown-arrow{transform:rotate(180deg)}
.other-card{border:2px solid var(--ink);padding:14px;margin-bottom:12px;background:var(--bg)}
.other-card:last-child{margin-bottom:0}

/* Tooltip on Sustainability Hover */
.bar-tooltip-wrap{position:relative;cursor:help}
.bar-tooltip-wrap:hover .tooltip-box{display:block}
.tooltip-box{display:none;position:absolute;left:0;top:100%;z-index:99;background:var(--bg);color:var(--ink);border:3px solid var(--ink);padding:12px;width:340px;box-shadow:6px 6px 0 var(--ink);margin-top:6px;font-size:.85rem}
.tooltip-title{font-weight:900;border-bottom:2px solid var(--ink);padding-bottom:4px;margin-bottom:8px;text-transform:uppercase;letter-spacing:.05em}
.tooltip-formula{font-size:.78rem;margin-bottom:8px;padding:4px;background:var(--mute);border:1px solid var(--ink)}
.rubric-row{display:grid;grid-template-columns:1fr auto;gap:4px;padding:2px 0;font-size:.8rem}
.rubric-detail{grid-column:1 / -1;font-size:.75rem;opacity:.8;margin-bottom:4px}
.rubric-total{border-top:2px solid var(--ink);margin-top:6px;padding-top:4px;font-weight:900;display:flex;justify-content:space-between}

/* Storage Card Box (Top 2 Crops) */
.storage-box{margin-top:12px;border:2px solid var(--ink);padding:10px 12px;background:var(--bg)}
.storage-hdr{font-weight:900;font-family:Helvetica,Arial,sans-serif;font-size:.95rem;text-transform:uppercase;letter-spacing:.03em;margin-bottom:6px;border-bottom:1px solid var(--ink);padding-bottom:3px}
.storage-item{margin:4px 0;font-size:.86rem;line-height:1.4}
.storage-item b{font-weight:700}

/* Brutalist Loading Animation & Status Box */
#loading-box{display:none;border:4px solid var(--ink);padding:24px 20px;background:var(--bg);box-shadow:8px 8px 0 var(--ink);margin-bottom:24px}
.load-title{font-family:Helvetica,Arial,sans-serif;font-weight:900;font-size:1.5rem;letter-spacing:-.02em;margin:0 0 8px;display:flex;align-items:center;justify-content:space-between}
.load-sub{font-size:.9rem;margin:0 0 16px;opacity:.85}
.load-track{height:22px;border:3px solid var(--ink);background:var(--bg);position:relative;overflow:hidden;margin-bottom:14px}
.load-stripes{height:100%;width:200%;background:repeating-linear-gradient(45deg,var(--ink),var(--ink) 14px,var(--bg) 14px,var(--bg) 28px);animation:stripes-scroll 1s linear infinite}
@keyframes stripes-scroll{0%{transform:translateX(0)}100%{transform:translateX(-28px)}}
.load-steps{display:grid;gap:6px;font-size:.85rem;border-top:2px dashed var(--ink);padding-top:10px}
.load-step{display:flex;align-items:center;gap:8px}
.load-step.active{font-weight:700}
.step-dot{display:inline-block;width:10px;height:10px;border:2px solid var(--ink);background:var(--ink);animation:blink .8s infinite alternate}
@keyframes blink{0%{opacity:.2}100%{opacity:1}}
"""

    results_html = ""
    climate_info_html = ""

    if results_payload:
        c = results_payload["climate"]
        temp = results_payload["temp"]
        rainfall = results_payload["rainfall"]
        ph = results_payload["ph"]
        suitable = results_payload["suitable"]
        unsuitable = results_payload["unsuitable"]

        climate_info_html = f"""
        <div class="climate-box">
            <div class="row"><span>Matched</span><span class="val">{escape_html(c['matched_location'])}</span></div>
            <div class="row"><span>Period</span><span class="val">{escape_html(c['period'])}</span></div>
            <div class="row"><span>Avg temp</span><span class="val">{temp:.1f} °C</span></div>
            <div class="row"><span>Annual rain</span><span class="val">{rainfall:.0f} mm</span></div>
        </div>
        """

        header_ctx = f'<div class="ctx"><b>pH {ph:.1f}</b> · <b>{temp:.1f} °C</b> · <b>{rainfall:.0f} mm/yr</b> · {escape_html(c["matched_location"])}</div>'

        if not suitable:
            cards_html = '<div class="empty">No crop in the database suits these conditions. Try another location or soil pH.</div>'
        else:
            cards = []
            medals = ["1st", "2nd", "3rd"]
            for i, r in enumerate(suitable[:3]):
                crop = r["crop"]
                is_first = ' first' if i == 0 else ''

                # Generate bar items with hover tooltip on Sustainability
                bar_items = []
                for k, v in r["parts"].items():
                    pct = round(v * 100)
                    if k == "Sustainability" and crop.name in SUSTAINABILITY_RUBRICS:
                        rub = SUSTAINABILITY_RUBRICS[crop.name]
                        n_c = round(0.40 * rub["n_fix"])
                        p_c = round(0.20 * rub["poor_soil"])
                        d_c = round(0.25 * rub["duration"])
                        g_c = round(0.15 * rub["ghg"])
                        tooltip_html = f"""
                        <div class="tooltip-box">
                            <div class="tooltip-title">Sustainability Rubric (Score: {rub['score']}/100)</div>
                            <div class="tooltip-formula">Score = 0.40(N-fix) + 0.20(Poor Soil) + 0.25(Duration) + 0.15(GHG)</div>
                            <div class="rubric-row">
                                <span><b>1. Nitrogen Fixation (40%)</b></span>
                                <span>+{n_c} pts</span>
                            </div>
                            <div class="rubric-detail">{rub['n_fix']}% · {escape_html(rub['n_fix_label'])}</div>
                            <div class="rubric-row">
                                <span><b>2. Marginal Soil Hardiness (20%)</b></span>
                                <span>+{p_c} pts</span>
                            </div>
                            <div class="rubric-detail">{rub['poor_soil']}% · {escape_html(rub['poor_soil_label'])}</div>
                            <div class="rubric-row">
                                <span><b>3. Crop Cycle / Rest (25%)</b></span>
                                <span>+{d_c} pts</span>
                            </div>
                            <div class="rubric-detail">{rub['duration']}% · {escape_html(rub['duration_label'])}</div>
                            <div class="rubric-row">
                                <span><b>4. GHG / Carbon Footprint (15%)</b></span>
                                <span>+{g_c} pts</span>
                            </div>
                            <div class="rubric-detail">{rub['ghg']}% · {escape_html(rub['ghg_label'])}</div>
                            <div class="rubric-total">
                                <span>Calculated Rating:</span>
                                <span>{rub['score']} / 100</span>
                            </div>
                        </div>
                        """
                        bar_items.append(f"""
                        <div class="bar bar-tooltip-wrap">
                            <span>{k} ℹ</span>
                            <div class="track"><div class="fill" style="width:{pct}%"></div></div>
                            <span>{pct}%</span>
                            {tooltip_html}
                        </div>
                        """)
                    else:
                        bar_items.append(f"""
                        <div class="bar">
                            <span>{k}</span>
                            <div class="track"><div class="fill" style="width:{pct}%"></div></div>
                            <span>{pct}%</span>
                        </div>
                        """)
                bars_html = "".join(bar_items)

                cycle_html = ""
                if i == 0:
                    cycle_html = f'<div class="cycle"><b>Crop cycle:</b> {escape_html(crop.season)} season. Sow in {escape_html(crop.sowing)}. Ready in {escape_html(crop.duration)}.</div>'

                # Storage instructions for top 3 recommendations
                storage_html = ""
                if i < 3 and crop.name in CROP_STORAGE:
                    st = CROP_STORAGE[crop.name]
                    storage_html = f"""
                    <div class="storage-box">
                        <div class="storage-hdr">Post-Harvest Storage & Shelf Life</div>
                        <div class="storage-item"><b>Shelf Life:</b> {escape_html(st['shelf_life'])}</div>
                        <div class="storage-item"><b>Safe Moisture:</b> {escape_html(st['moisture'])}</div>
                        <div class="storage-item"><b>Storage Method:</b> {escape_html(st['method'])}</div>
                    </div>
                    """

                cards.append(f"""
                <div class="card{is_first}">
                    <div class="top">
                        <div>
                            <span class="rank">{medals[i]}</span>
                            <div class="name">{escape_html(crop.name)}</div>
                            <div class="meta">{escape_html(crop.category)} crop. {escape_html(r['label'])}.</div>
                        </div>
                        <div class="score">{r['score']}<small> / 100</small></div>
                    </div>
                    <div class="bars">{bars_html}</div>
                    {cycle_html}
                    {storage_html}
                </div>
                """)
            cards_html = "".join(cards)

        others_html = ""
        others = suitable[3:]
        if others:
            other_cards = []
            for j, r in enumerate(others, start=4):
                crop = r["crop"]
                # Generate bar items for other options (including sustainability hover tooltip)
                b_items = []
                for k, v in r["parts"].items():
                    pct = round(v * 100)
                    if k == "Sustainability" and crop.name in SUSTAINABILITY_RUBRICS:
                        rub = SUSTAINABILITY_RUBRICS[crop.name]
                        n_c = round(0.40 * rub["n_fix"])
                        p_c = round(0.20 * rub["poor_soil"])
                        d_c = round(0.25 * rub["duration"])
                        g_c = round(0.15 * rub["ghg"])
                        t_html = f"""
                        <div class="tooltip-box">
                            <div class="tooltip-title">Sustainability Rubric (Score: {rub['score']}/100)</div>
                            <div class="tooltip-formula">Score = 0.40(N-fix) + 0.20(Poor Soil) + 0.25(Duration) + 0.15(GHG)</div>
                            <div class="rubric-row"><span><b>1. Nitrogen Fixation (40%)</b></span><span>+{n_c} pts</span></div>
                            <div class="rubric-detail">{rub['n_fix']}% · {escape_html(rub['n_fix_label'])}</div>
                            <div class="rubric-row"><span><b>2. Marginal Soil Hardiness (20%)</b></span><span>+{p_c} pts</span></div>
                            <div class="rubric-detail">{rub['poor_soil']}% · {escape_html(rub['poor_soil_label'])}</div>
                            <div class="rubric-row"><span><b>3. Crop Cycle / Rest (25%)</b></span><span>+{d_c} pts</span></div>
                            <div class="rubric-detail">{rub['duration']}% · {escape_html(rub['duration_label'])}</div>
                            <div class="rubric-row"><span><b>4. GHG / Carbon Footprint (15%)</b></span><span>+{g_c} pts</span></div>
                            <div class="rubric-detail">{rub['ghg']}% · {escape_html(rub['ghg_label'])}</div>
                            <div class="rubric-total"><span>Calculated Rating:</span><span>{rub['score']} / 100</span></div>
                        </div>
                        """
                        b_items.append(f"""
                        <div class="bar bar-tooltip-wrap">
                            <span>{k} ℹ</span>
                            <div class="track"><div class="fill" style="width:{pct}%"></div></div>
                            <span>{pct}%</span>
                            {t_html}
                        </div>
                        """)
                    else:
                        b_items.append(f"""
                        <div class="bar">
                            <span>{k}</span>
                            <div class="track"><div class="fill" style="width:{pct}%"></div></div>
                            <span>{pct}%</span>
                        </div>
                        """)
                other_bars_html = "".join(b_items)

                other_cycle_html = f'<div class="cycle"><b>Crop cycle:</b> {escape_html(crop.season)} season. Sow in {escape_html(crop.sowing)}. Ready in {escape_html(crop.duration)}.</div>'

                other_storage_html = ""
                if crop.name in CROP_STORAGE:
                    st = CROP_STORAGE[crop.name]
                    other_storage_html = f"""
                    <div class="storage-box">
                        <div class="storage-hdr">Post-Harvest Storage & Shelf Life</div>
                        <div class="storage-item"><b>Shelf Life:</b> {escape_html(st['shelf_life'])}</div>
                        <div class="storage-item"><b>Safe Moisture:</b> {escape_html(st['moisture'])}</div>
                        <div class="storage-item"><b>Storage Method:</b> {escape_html(st['method'])}</div>
                    </div>
                    """

                other_cards.append(f"""
                <div class="other-card">
                    <div class="top">
                        <div>
                            <span class="rank">{j}th</span>
                            <div class="name">{escape_html(crop.name)}</div>
                            <div class="meta">{escape_html(crop.category)} crop. {escape_html(r['label'])}.</div>
                        </div>
                        <div class="score">{r['score']}<small> / 100</small></div>
                    </div>
                    <div class="bars">{other_bars_html}</div>
                    {other_cycle_html}
                    {other_storage_html}
                </div>
                """)

            cards_joined = "".join(other_cards)
            others_html = f"""
            <h2>Other options ({len(others)} crops)</h2>
            <div class="dropdown-menu">
                <div class="dropdown-toggle">
                    <span>HOVER TO VIEW DETAILED CARDS FOR ALL OTHER SUITABLE CROPS</span>
                    <span class="dropdown-arrow">▼</span>
                </div>
                <div class="dropdown-content">
                    {cards_joined}
                </div>
            </div>
            """

        unsuitable_html = ""
        if unsuitable:
            rows = "".join([
                f'<div class="no">{escape_html(r["crop"].name)}: {escape_html("; ".join(r["reasons"]))}</div>'
                for r in unsuitable
            ])
            unsuitable_html = f'<h2>Not suitable for your field</h2>{rows}'

        results_html = f"{header_ctx}{cards_html}{others_html}{unsuitable_html}"
    else:
        results_html = '<div class="empty">Enter your farm details and press the button. Ranked crops appear here.</div>'

    error_block = f'<div class="err" role="alert">{escape_html(error_msg)}</div>' if error_msg else ""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Sustainable Crop Recommendation - Sow What?</title>
<style>{styles}</style>
</head>
<body>
<div class="wrap">
  <h1>Sow What?</h1>
  <p class="sub">Enter your location to auto-fetch climate data. Ten crops are scored out of 100: 70% fit to your conditions, 30% effect on soil health.</p>

  <div class="grid">
    <div>
      <form id="cropForm" method="POST" action="/" onsubmit="showLoading()">
        <div class="lab">Location (city or village)</div>
        <input id="placeInput" name="place" type="text" value="{escape_html(place_input)}" placeholder="e.g. Kochi, India" required>
        {climate_info_html}
        <div class="lab">Soil pH (3 to 10)</div>
        <input name="ph" type="number" step="0.1" min="3" max="10" value="{escape_html(ph_input)}" required>
        {error_block}
        <button id="goBtn" type="submit" class="go">Find my crops</button>
      </form>
    </div>
    <div id="out">
      <div id="loading-box">
        <div class="load-title">
          <span>FETCHING CLIMATE ARCHIVE...</span>
          <span>[LIVE]</span>
        </div>
        <p class="load-sub" id="loadLocationText">Connecting to Open-Meteo geocoding & historical meteorological databases...</p>
        <div class="load-track">
          <div class="load-stripes"></div>
        </div>
        <div class="load-steps">
          <div class="load-step active"><span class="step-dot"></span> 1. Geocoding coordinates for entered location</div>
          <div class="load-step"><span class="step-dot"></span> 2. Aggregating daily temperature & precipitation archives</div>
          <div class="load-step"><span class="step-dot"></span> 3. Calculating multi-criteria compatibility & soil health scores</div>
        </div>
      </div>
      <div id="results-content">
        {results_html}
      </div>
    </div>
  </div>

  <p class="foot">Climate data from Open-Meteo archive API. Crop values sourced from ICAR, ICRISAT, FAO, TNAU and state agriculture departments. Cross-check before relying on the results.</p>
</div>
<script>
function showLoading() {{
  var btn = document.getElementById('goBtn');
  var loadBox = document.getElementById('loading-box');
  var resContent = document.getElementById('results-content');
  var placeVal = document.getElementById('placeInput').value.trim();

  if (btn) {{
    btn.disabled = true;
    btn.textContent = "Analysing field data...";
  }}
  if (loadBox) {{
    loadBox.style.display = 'block';
  }}
  if (resContent) {{
    resContent.style.opacity = '0.35';
    resContent.style.pointerEvents = 'none';
  }}
  if (placeVal) {{
    var locText = document.getElementById('loadLocationText');
    if (locText) {{
      locText.textContent = "Connecting to meteorological satellites & archive for '" + placeVal + "'...";
    }}
  }}
}}
</script>
</body>
</html>
"""
    return html


class CropPickerHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        page = render_html_page()
        self.wfile.write(page.encode("utf-8"))

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length).decode("utf-8")
        params = urllib.parse.parse_qs(post_data)

        place = params.get("place", [""])[0].strip()
        ph_str = params.get("ph", ["6.5"])[0].strip()

        error_msg = ""
        results_payload = None

        try:
            ph = float(ph_str)
            if not (3.0 <= ph <= 10.0):
                error_msg = "Soil pH must be between 3 and 10."
        except ValueError:
            error_msg = "Soil pH must be a valid number."

        if not error_msg:
            if not place:
                error_msg = "Please enter a location name."
            else:
                try:
                    climate = climate_for_place(place)
                    temp = climate["avg_daily_temp_c"]
                    rainfall = climate["avg_annual_rainfall_mm"]
                    if temp is None or rainfall is None:
                        error_msg = f"Incomplete climate data returned for '{place}'."
                    else:
                        suitable, unsuitable = recommend(ph, temp, rainfall)
                        results_payload = {
                            "climate": climate,
                            "ph": ph,
                            "temp": temp,
                            "rainfall": rainfall,
                            "suitable": suitable,
                            "unsuitable": unsuitable,
                        }
                except Exception as e:
                    error_msg = f"Error fetching climate data: {e}"

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        page = render_html_page(place_input=place, ph_input=ph_str, error_msg=error_msg, results_payload=results_payload)
        self.wfile.write(page.encode("utf-8"))


def run_server(port=8000):
    server_address = ("", port)
    httpd = HTTPServer(server_address, CropPickerHandler)
    print("=" * 62)
    print(f" Crop Picker Web App running at http://localhost:{port}")
    print(" Open http://localhost:8000 in your browser.")
    print(" Press Ctrl+C to stop the server.")
    print("=" * 62)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        httpd.server_close()


if __name__ == "__main__":
    port = 8000
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        port = int(sys.argv[1])
    run_server(port)