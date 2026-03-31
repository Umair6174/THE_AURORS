# ============================================================
#  LumiNet AI — Frontend v2.1  (app.py)
#  Fixed: REST API polling as primary data source
#         Socket.IO as secondary (live push)
#         No more blank dashboard on load
# ============================================================
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import numpy as np
import time, socketio as sio_lib, threading, os, base64, json, io, requests
from datetime import datetime, timedelta

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors as rl_colors
    PDF_OK = True
except ImportError:
    PDF_OK = False

BACKEND_URL        = "http://localhost:5000"
BACKEND_SOCKET_URL = "http://localhost:5000"

PAGE_ICON = "⚡"
logo_file = next((f"logo.{ext}" for ext in ["png","jpg","jpeg"] if os.path.exists(f"logo.{ext}")), None)
if logo_file: PAGE_ICON = logo_file

st.set_page_config(page_title="LumiNet AI", page_icon=PAGE_ICON,
                   layout="wide", initial_sidebar_state="expanded")

# ══════════════════════════════════════════════════════════════
#  SESSION STATE
# ══════════════════════════════════════════════════════════════
DEFAULTS = {
    "logged_in": False, "role": None, "username": None,
    "page": "🌐 Global Overview", "theme": "dark",
    "alert_log": [], "node_overrides": {},
    "user_db": [
        {"username":"admin",  "password":"admin123","role":"Admin",      "active":True},
        {"username":"tech",   "password":"tech123", "role":"Technician", "active":True},
        {"username":"viewer", "password":"view123", "role":"Viewer",     "active":True},
    ],
    "tasks_db": [
        {"id":1001,"node":"LN-100","urgency":"CRITICAL",
         "desc":"Wire cut / Power loss detected. Inspect physical switch.",
         "assigned_to":"tech","status":"Pending","time":"2026-03-30 08:30"},
        {"id":1002,"node":"LN-101","urgency":"WARNING",
         "desc":"Clean LDR sensor to fix ambient detection.",
         "assigned_to":"tech","status":"Completed","time":"2026-03-29 14:15"},
    ],
    "hist_data": None,
    "backend_online": False,
    "hardware_data": None,
    "traffic_data": {"density":0.0,"count":0,"source":"none"},
    "socket_connected": False,
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── Historical data (generated once) ─────────────────────────
if st.session_state.hist_data is None:
    rng  = np.random.default_rng(42)
    days = pd.date_range(end=datetime.now(), periods=7*24, freq="h")
    hx   = np.array([d.hour for d in days])
    trad = np.clip(95+15*np.sin((hx-3)*np.pi/12)+rng.normal(0,3,len(days)),60,130)
    lum  = np.clip(trad*(0.35+0.25*np.abs(np.sin((hx-6)*np.pi/12)))+rng.normal(0,2,len(days)),15,80)
    st.session_state.hist_data = pd.DataFrame({
        "timestamp":  days,
        "traditional":trad.round(2),
        "luminet":    lum.round(2),
        "savings_pct":((trad-lum)/trad*100).round(1),
        "co2_saved":  ((trad-lum)*0.48/1000).round(4),
    })

# ══════════════════════════════════════════════════════════════
#  REST API POLLING  ← PRIMARY data source, runs every refresh
# ══════════════════════════════════════════════════════════════
def fetch_backend_state():
    """Poll backend REST API on every Streamlit run.
    This is the primary data path — reliable, no socket needed."""
    try:
        r = requests.get(f"{BACKEND_URL}/api/state", timeout=2)
        if r.status_code == 200:
            st.session_state.hardware_data = r.json()
            st.session_state.backend_online = True
    except Exception:
        st.session_state.backend_online = False

    try:
        r = requests.get(f"{BACKEND_URL}/api/traffic", timeout=2)
        if r.status_code == 200:
            st.session_state.traffic_data = r.json()
    except Exception:
        pass

    try:
        r = requests.get(f"{BACKEND_URL}/api/alerts", timeout=2)
        if r.status_code == 200:
            fetched = r.json()
            # Merge with local alerts (local ones may have been pushed by frontend)
            existing_times = {a["time"]+a["msg"] for a in st.session_state.alert_log}
            for a in fetched:
                if a["time"]+a["msg"] not in existing_times:
                    st.session_state.alert_log.append(a)
            st.session_state.alert_log = sorted(
                st.session_state.alert_log,
                key=lambda x: x["time"], reverse=True
            )[:50]
    except Exception:
        pass

# Run on every Streamlit script execution
fetch_backend_state()

# ══════════════════════════════════════════════════════════════
#  SOCKET.IO CLIENT  ← SECONDARY (live push updates)
# ══════════════════════════════════════════════════════════════
if "sio" not in st.session_state:
    _sio = sio_lib.Client(logger=False, engineio_logger=False)
    st.session_state.sio = _sio

    @_sio.on("sensor_data")
    def _on_sensor(data):
        st.session_state.hardware_data = data

    @_sio.on("traffic_update")
    def _on_traffic(data):
        st.session_state.traffic_data = data

    @_sio.on("alert")
    def _on_alert(data):
        st.session_state.alert_log.insert(0, data)
        st.session_state.alert_log = st.session_state.alert_log[:50]

    @_sio.on("connect")
    def _on_connect():
        st.session_state.socket_connected = True

    @_sio.on("disconnect")
    def _on_disconnect():
        st.session_state.socket_connected = False

    def _socket_loop():
        while True:
            try:
                if not st.session_state.sio.connected:
                    st.session_state.sio.connect(BACKEND_SOCKET_URL)
                    st.session_state.sio.wait()
            except Exception:
                pass
            time.sleep(5)

    threading.Thread(target=_socket_loop, daemon=True).start()

# ══════════════════════════════════════════════════════════════
#  THEME
# ══════════════════════════════════════════════════════════════
THEMES = {
    "dark": {
        "--bg-primary":"#030712","--bg-secondary":"#0d1117","--bg-card":"#0f1923",
        "--bg-sidebar":"linear-gradient(180deg,#050d1a 0%,#0a1628 40%,#050d1a 100%)",
        "--accent-cyan":"#00f5ff","--accent-green":"#00ff88","--accent-orange":"#ff6b35",
        "--accent-yellow":"#ffd700","--accent-red":"#ff3366",
        "--text-primary":"#e2e8f0","--text-muted":"#64748b",
        "--grid-line":"rgba(0,245,255,0.05)","--border-card":"rgba(0,245,255,0.18)",
    },
    "light": {
        "--bg-primary":"#f0f4f8","--bg-secondary":"#e2e8f0","--bg-card":"#ffffff",
        "--bg-sidebar":"linear-gradient(180deg,#1e3a5f 0%,#0a1628 100%)",
        "--accent-cyan":"#0284c7","--accent-green":"#16a34a","--accent-orange":"#ea580c",
        "--accent-yellow":"#d97706","--accent-red":"#dc2626",
        "--text-primary":"#0f172a","--text-muted":"#64748b",
        "--grid-line":"rgba(2,132,199,0.07)","--border-card":"rgba(2,132,199,0.2)",
    }
}

def inject_css():
    th  = THEMES[st.session_state.theme]
    cvs = "\n".join(f"  {k}:{v};" for k,v in th.items())
    st.markdown(f"""<style>
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;600;700;900&family=Share+Tech+Mono&family=Syne:wght@400;600;700&display=swap');
:root {{ {cvs} }}
html,body,[class*="css"]{{background-color:var(--bg-primary)!important;color:var(--text-primary)!important;font-family:'Syne',sans-serif!important;}}
.stDeployButton{{display:none!important;}} #MainMenu{{visibility:hidden;}} footer{{visibility:hidden;}}
.stApp{{background-color:var(--bg-primary)!important;background-image:linear-gradient(var(--grid-line) 1px,transparent 1px),linear-gradient(90deg,var(--grid-line) 1px,transparent 1px);background-size:40px 40px;}}
.login-box{{background:var(--bg-card);border:1px solid var(--border-card);border-radius:20px;padding:3rem 2.5rem;box-shadow:0 8px 48px rgba(0,0,0,0.5);text-align:center;max-width:420px;margin:auto;}}
.login-title{{font-family:'Orbitron',sans-serif;font-size:1.6rem;font-weight:900;background:linear-gradient(135deg,var(--accent-cyan),var(--accent-green));-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:0.25rem;}}
.login-sub{{font-family:'Share Tech Mono',monospace;font-size:0.68rem;color:var(--text-muted);letter-spacing:0.25em;margin-bottom:2rem;}}
.login-box button{{background:rgba(0,245,255,0.08)!important;border:1px solid var(--accent-cyan)!important;color:var(--accent-cyan)!important;font-family:'Orbitron',sans-serif!important;font-size:0.8rem!important;letter-spacing:.15em!important;border-radius:8px!important;transition:all .25s!important;}}
.login-box button:hover{{background:var(--accent-cyan)!important;color:#000!important;box-shadow:0 0 20px rgba(0,245,255,.4)!important;}}
[data-testid="stSidebar"]{{background:var(--bg-sidebar)!important;border-right:1px solid rgba(0,245,255,.15)!important;}}
[data-testid="stSidebar"] *{{color:#e2e8f0!important;}}
.sidebar-logo{{text-align:center;padding:1.5rem .5rem 1rem;border-bottom:1px solid rgba(0,245,255,.12);margin-bottom:1.25rem;}}
.sidebar-logo h1{{font-family:'Orbitron',sans-serif!important;font-size:1rem!important;background:linear-gradient(135deg,var(--accent-cyan),var(--accent-green));-webkit-background-clip:text;-webkit-text-fill-color:transparent;margin:.5rem 0 0;}}
[data-testid="stRadio"]>div>label{{padding:.6rem 1rem!important;border-radius:8px!important;font-size:.85rem!important;cursor:pointer!important;transition:background .2s!important;border:1px solid transparent!important;}}
[data-testid="stRadio"]>div>label:hover{{background:rgba(0,245,255,.08)!important;border-color:rgba(0,245,255,.2)!important;}}
[data-testid="stRadio"]>div [aria-checked="true"]{{background:rgba(0,245,255,.12)!important;border:1px solid rgba(0,245,255,.35)!important;color:var(--accent-cyan)!important;box-shadow:0 0 12px rgba(0,245,255,.12)!important;}}
[data-testid="stRadio"]>label{{display:none;}}
.sys-status{{display:flex;align-items:center;justify-content:center;gap:.5rem;padding:.4rem 1rem;margin:0 .75rem 1.25rem;background:rgba(0,255,136,.08);border:1px solid rgba(0,255,136,.3);border-radius:20px;font-family:'Share Tech Mono',monospace;font-size:.68rem;color:var(--accent-green)!important;}}
.sys-status.fault{{color:var(--accent-red)!important;border-color:rgba(255,51,102,.4);background:rgba(255,51,102,.1);}}
.sys-status.offline{{color:#64748b!important;border-color:#334155;background:rgba(0,0,0,.4);}}
.pulse-dot{{width:8px;height:8px;background:var(--accent-green);border-radius:50%;display:inline-block;box-shadow:0 0 8px var(--accent-green);animation:blink 1.2s ease-in-out infinite;}}
@keyframes blink{{0%,100%{{opacity:1}}50%{{opacity:.2}}}}
.sidebar-stat{{margin:0 .6rem .6rem;padding:.7rem 1rem;background:rgba(0,245,255,.04);border:1px solid rgba(0,245,255,.1);border-radius:8px;}}
.s-label{{font-family:'Share Tech Mono',monospace;font-size:.58rem;color:#64748b;}}
.s-value{{font-family:'Orbitron',sans-serif;font-size:1rem;color:var(--accent-cyan);margin-top:.15rem;}}
.kpi-card{{background:var(--bg-card);border:1px solid var(--border-card);border-radius:12px;padding:1.1rem 1.3rem;position:relative;box-shadow:0 4px 24px rgba(0,0,0,.3);transition:transform .2s,box-shadow .2s;overflow:hidden;}}
.kpi-card::before{{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(90deg,var(--accent-cyan),var(--accent-green));opacity:.6;}}
.kpi-card:hover{{transform:translateY(-2px);box-shadow:0 8px 32px rgba(0,245,255,.12);}}
.kpi-card.green{{border-color:rgba(0,255,136,.25);}} .kpi-card.orange{{border-color:rgba(255,107,53,.25);}} .kpi-card.red{{border-color:rgba(255,51,102,.3);}} .kpi-card.yellow{{border-color:rgba(255,215,0,.25);}} .kpi-card.cyan{{border-color:rgba(0,245,255,.25);}}
.kpi-label{{font-family:'Share Tech Mono',monospace;font-size:.65rem;color:var(--text-muted);margin-bottom:.4rem;letter-spacing:.05em;}}
.kpi-value{{font-family:'Orbitron',sans-serif;font-size:1.9rem;font-weight:700;line-height:1.1;}}
.kpi-delta{{font-family:'Share Tech Mono',monospace;font-size:.65rem;margin-top:.3rem;}}
.kpi-value.cyan{{color:var(--accent-cyan);}} .kpi-value.green{{color:var(--accent-green);}} .kpi-value.orange{{color:var(--accent-orange);}} .kpi-value.red{{color:var(--accent-red);}} .kpi-value.yellow{{color:var(--accent-yellow);}}
.chart-container{{background:var(--bg-card);border:1px solid rgba(0,245,255,.1);border-radius:12px;padding:1.1rem;}}
.section-header{{display:flex;align-items:center;gap:.75rem;margin:1.5rem 0 1rem;padding-bottom:.5rem;border-bottom:1px solid rgba(0,245,255,.08);}}
.section-header h3{{font-family:'Orbitron',sans-serif!important;font-size:.85rem!important;margin:0!important;letter-spacing:.05em;}}
.page-title-block{{padding:.5rem 0 1.25rem;border-bottom:1px solid rgba(0,245,255,.1);margin-bottom:1.5rem;}}
.page-title-block h2{{font-family:'Orbitron',sans-serif!important;font-size:1.4rem!important;margin:0!important;background:linear-gradient(90deg,var(--accent-cyan),#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent;}}
.subtitle{{font-family:'Share Tech Mono',monospace;font-size:.65rem;color:var(--text-muted);letter-spacing:.18em;margin-top:.2rem;}}
.node-card{{background:var(--bg-card);border-radius:12px;padding:1rem .75rem;text-align:center;border:1px solid rgba(0,245,255,.1);transition:border-color .3s,box-shadow .3s;}}
.node-card:hover{{border-color:rgba(0,245,255,.35);box-shadow:0 4px 20px rgba(0,245,255,.08);}}
.node-card.offline{{border-color:#334155;opacity:.55;}}
.node-card.fault{{border-color:rgba(255,51,102,.4);box-shadow:0 0 20px rgba(255,51,102,.1);animation:fault-pulse 2s ease-in-out infinite;}}
@keyframes fault-pulse{{0%,100%{{box-shadow:0 0 10px rgba(255,51,102,.1)}}50%{{box-shadow:0 0 25px rgba(255,51,102,.3)}}}}
.task-card{{background:var(--bg-card);border-left:4px solid var(--accent-cyan);border:1px solid rgba(0,245,255,.1);border-left-width:4px;padding:1rem 1.1rem;margin-bottom:.75rem;border-radius:8px;}}
.task-card.critical{{border-left-color:var(--accent-red);}} .task-card.warning{{border-left-color:var(--accent-yellow);}} .task-card.completed{{border-left-color:var(--accent-green);opacity:.65;}}
.alert-item{{background:rgba(255,51,102,.07);border:1px solid rgba(255,51,102,.25);border-left:3px solid var(--accent-red);border-radius:8px;padding:.75rem 1rem;margin-bottom:.5rem;font-family:'Share Tech Mono',monospace;font-size:.78rem;}}
.alert-item.warn{{background:rgba(255,215,0,.07);border-color:rgba(255,215,0,.25);border-left-color:var(--accent-yellow);}}
.alert-item.info{{background:rgba(0,245,255,.06);border-color:rgba(0,245,255,.18);border-left-color:var(--accent-cyan);}}
.cyber-divider{{height:1px;background:linear-gradient(90deg,transparent,rgba(0,245,255,.2),transparent);margin:1.25rem 0;}}
.live-ticker{{display:inline-flex;align-items:center;gap:.5rem;font-family:'Share Tech Mono',monospace;font-size:.68rem;color:var(--text-muted);padding:.3rem .8rem;background:rgba(0,0,0,.3);border:1px solid rgba(0,245,255,.1);border-radius:20px;}}
.source-badge{{display:inline-block;font-family:'Share Tech Mono';font-size:.6rem;padding:.15rem .5rem;border-radius:4px;margin-left:.4rem;}}
.source-badge.wifi{{background:rgba(0,245,255,.12);color:var(--accent-cyan);border:1px solid rgba(0,245,255,.3);}}
.source-badge.serial{{background:rgba(255,215,0,.12);color:var(--accent-yellow);border:1px solid rgba(255,215,0,.3);}}
.source-badge.offline{{background:rgba(100,116,139,.1);color:#64748b;border:1px solid #334155;}}
.user-row{{background:var(--bg-card);border:1px solid rgba(0,245,255,.1);border-radius:8px;padding:.75rem 1rem;margin-bottom:.5rem;}}
.role-badge{{font-family:'Orbitron',sans-serif;font-size:.6rem;padding:.2rem .6rem;border-radius:20px;font-weight:700;}}
.role-badge.admin{{background:rgba(0,245,255,.15);color:var(--accent-cyan);border:1px solid rgba(0,245,255,.3);}}
.role-badge.tech{{background:rgba(0,255,136,.12);color:var(--accent-green);border:1px solid rgba(0,255,136,.3);}}
.role-badge.viewer{{background:rgba(255,215,0,.12);color:var(--accent-yellow);border:1px solid rgba(255,215,0,.3);}}
.traffic-bar-wrap{{background:rgba(0,0,0,.3);border:1px solid rgba(0,245,255,.1);border-radius:20px;height:12px;overflow:hidden;margin:.3rem 0;}}
.traffic-bar{{height:100%;border-radius:20px;transition:width .5s ease;}}
.backend-banner{{padding:.5rem 1rem;border-radius:8px;font-family:'Share Tech Mono',monospace;font-size:.72rem;margin-bottom:1rem;text-align:center;}}
.backend-banner.online{{background:rgba(0,255,136,.08);border:1px solid rgba(0,255,136,.3);color:#00ff88;}}
.backend-banner.offline{{background:rgba(255,51,102,.08);border:1px solid rgba(255,51,102,.3);color:#ff3366;}}
</style>""", unsafe_allow_html=True)

inject_css()

# ══════════════════════════════════════════════════════════════
#  DATA HELPERS
# ══════════════════════════════════════════════════════════════
NODE_IDS  = ["LN-100","LN-101","LN-102"]
COORDS    = [(19.87588,75.3402),(19.87595,75.3416),(19.87620,75.3430)]
S_COLORS  = {"Healthy":"#00ff88","Low Ambient":"#ffd700","Fault":"#ff3366","Offline":"#64748b"}
S_ICONS   = {"Healthy":"💡","Low Ambient":"🌙","Fault":"⚠️","Offline":"🔌"}
S_CLS     = {"Healthy":"healthy","Low Ambient":"warning","Fault":"fault","Offline":"offline"}

PLOTLY = dict(
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="Share Tech Mono, monospace",color="#94a3b8",size=11),
    xaxis=dict(gridcolor="rgba(0,245,255,0.07)",zerolinecolor="rgba(0,245,255,0.07)"),
    yaxis=dict(gridcolor="rgba(0,245,255,0.07)",zerolinecolor="rgba(0,245,255,0.07)"),
)

def get_node_data():
    hw  = st.session_state.hardware_data or {}
    trf = st.session_state.traffic_data.get("density",0.0)
    nodes = []
    for node_id, (lat,lon) in zip(NODE_IDS,COORDS):
        n = hw.get(node_id,{})
        s   = n.get("status","Offline")
        b   = n.get("brightness",0.0)
        v   = n.get("voltage",0.0)
        u   = n.get("uptime_h",0)
        a   = n.get("ambient_light",0)
        t_d = n.get("traffic_density", trf)
        src = n.get("source","—")
        ovr = st.session_state.node_overrides.get(node_id)
        if ovr is not None: b = ovr
        nodes.append({
            "node_id":node_id,"lat":lat,"lon":lon,
            "status":s,"color":S_COLORS.get(s,"#fff"),
            "icon":S_ICONS.get(s,"🔌"),"css_class":S_CLS.get(s,""),
            "brightness":b,"voltage":v,"uptime_h":u,
            "ambient":a,"traffic":t_d,"source":src,
        })
    return nodes

def get_kpis(nodes):
    faults = sum(1 for n in nodes if n["status"]=="Fault")
    online = sum(1 for n in nodes if n["status"]!="Offline")
    avg_b  = np.mean([n["brightness"] for n in nodes if n["status"]!="Offline"]) if online else 0
    es     = round(max(0,100-avg_b),1) if online else 0.0
    up     = 100.0 if faults==0 and online>0 else (98.5 if online>0 else 0.0)
    return {"energy_saved":es,"latency":0.12 if online else 0.0,
            "faults":faults,"uptime":up,"nodes_online":online,
            "co2_saved":round(es*0.48,1),"avg_brightness":round(avg_b,1)}

def get_energy_df(online):
    times = [datetime.now()-timedelta(minutes=30*i) for i in range(48,0,-1)]
    if online==0:
        return pd.DataFrame({"timestamp":times,"traditional":np.zeros(48),"luminet":np.zeros(48),"savings_pct":np.zeros(48)})
    h = np.array([t.hour+t.minute/60 for t in times])
    trad = np.clip(95+15*np.sin((h-3)*np.pi/12),60,130)
    lum  = np.clip(trad*(0.35+0.25*np.abs(np.sin((h-6)*np.pi/12))),15,80)
    return pd.DataFrame({"timestamp":times,"traditional":trad.round(2),
                         "luminet":lum.round(2),"savings_pct":((trad-lum)/trad*100).round(1)})

def push_local_alert(msg,level="info"):
    st.session_state.alert_log.insert(0,{"time":datetime.now().strftime("%H:%M:%S"),"msg":msg,"level":level})
    st.session_state.alert_log = st.session_state.alert_log[:50]

def send_manual_brightness(node_id, brightness):
    """Send override to backend — tries Socket.IO first, falls back to REST."""
    # Try Socket.IO
    try:
        if st.session_state.sio.connected:
            st.session_state.sio.emit("manual_brightness",{"node_id":node_id,"brightness":brightness})
            return
    except Exception:
        pass
    # Fallback: REST POST
    try:
        requests.post(f"{BACKEND_URL}/api/manual_brightness",
                      json={"node_id":node_id,"brightness":brightness}, timeout=2)
    except Exception:
        pass

# ══════════════════════════════════════════════════════════════
#  LOGIN
# ══════════════════════════════════════════════════════════════
if not st.session_state.logged_in:
    c1,c2,c3 = st.columns([1,1.2,1])
    with c2:
        st.markdown('<div class="login-box">', unsafe_allow_html=True)
        if logo_file:
            with open(logo_file,"rb") as f: enc=base64.b64encode(f.read()).decode()
            st.markdown(f'<img src="data:image/png;base64,{enc}" style="width:80px;display:block;margin:0 auto 1rem;">', unsafe_allow_html=True)
        else:
            st.markdown('<div style="font-size:3.5rem;text-align:center;margin-bottom:.5rem;">⚡</div>',unsafe_allow_html=True)
        st.markdown('<div class="login-title">LumiNet AI</div><div class="login-sub">SECURE ACCESS PORTAL · v2.1</div>',unsafe_allow_html=True)

        # Backend status on login page
        if st.session_state.backend_online:
            st.markdown('<div class="backend-banner online">● BACKEND ONLINE — http://localhost:5000</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="backend-banner offline">○ BACKEND OFFLINE — Start backend.py first</div>', unsafe_allow_html=True)

        roles    = list({u["role"] for u in st.session_state.user_db if u["active"]})
        sel_role = st.selectbox("Role",roles,label_visibility="collapsed")
        u_in     = st.text_input("Username",placeholder="Username",label_visibility="collapsed")
        p_in     = st.text_input("Password",type="password",placeholder="Password",label_visibility="collapsed")
        st.markdown("<br>",unsafe_allow_html=True)
        if st.button("AUTHENTICATE",use_container_width=True):
            match = next((u for u in st.session_state.user_db
                          if u["username"]==u_in and u["password"]==p_in
                          and u["role"]==sel_role and u["active"]),None)
            if match:
                st.session_state.logged_in=True
                st.session_state.role=match["role"]
                st.session_state.username=match["username"]
                push_local_alert(f"User '{u_in}' logged in","info")
                st.rerun()
            else:
                st.error("❌ Access Denied")
        st.markdown('</div>',unsafe_allow_html=True)
    st.stop()

# ══════════════════════════════════════════════════════════════
#  SIDEBAR
# ══════════════════════════════════════════════════════════════
with st.sidebar:
    if logo_file:
        with open(logo_file,"rb") as f: enc=base64.b64encode(f.read()).decode()
        st.markdown(f'<div class="sidebar-logo"><img src="data:image/png;base64,{enc}" style="width:65px;margin-bottom:8px;"><h1>LumiNet AI</h1></div>',unsafe_allow_html=True)
    else:
        st.markdown('<div class="sidebar-logo"><h1>⚡ LumiNet AI</h1></div>',unsafe_allow_html=True)

    NAV_ALL = ["🌐 Global Overview","🗺️ Node Cluster Map","🔴 Fault Diagnostics",
               "📋 Task Management","🧠 AI Analytics","📈 Historical Trends",
               "🔔 Alert Center","👥 User Management"]
    if st.session_state.role=="Viewer":
        nav_opts=["🌐 Global Overview","🗺️ Node Cluster Map","📈 Historical Trends"]
    elif st.session_state.role=="Technician":
        nav_opts=["🌐 Global Overview","🗺️ Node Cluster Map","🔴 Fault Diagnostics",
                  "📋 Task Management","🧠 AI Analytics","📈 Historical Trends","🔔 Alert Center"]
    else:
        nav_opts=NAV_ALL

    if st.session_state.page not in nav_opts:
        st.session_state.page=nav_opts[0]

    unread = len(st.session_state.alert_log)
    nav_disp = [f"🔔 Alert Center {'🔴' if unread else ''}" if p=="🔔 Alert Center" else p for p in nav_opts]
    sel_disp = st.radio("nav",nav_disp,label_visibility="collapsed",
                        index=nav_opts.index(st.session_state.page) if st.session_state.page in nav_opts else 0)
    st.session_state.page = nav_opts[nav_disp.index(sel_disp)]

    st.markdown('<div class="cyber-divider"></div>',unsafe_allow_html=True)

    @st.fragment(run_every=3)
    def sidebar_live():
        nodes=get_node_data(); kpis=get_kpis(nodes)
        trf=st.session_state.traffic_data
        sock_ok=st.session_state.socket_connected
        be_ok=st.session_state.backend_online
        faults=kpis["faults"]
        if not be_ok:
            sc,st_txt,clr="sys-status offline","⚠ BACKEND OFFLINE","#64748b"
        elif faults>0:
            sc,st_txt,clr="sys-status fault",f"⚠ {faults} FAULT(S)","#ff3366"
        else:
            sc,st_txt,clr="sys-status","● SYSTEM NOMINAL","#00ff88"
        trf_clr="#00ff88" if trf["density"]<40 else "#ffd700" if trf["density"]<70 else "#ff3366"
        st.markdown(f"""
        <div class="{sc}"><span class="pulse-dot"></span>&nbsp;{st_txt}</div>
        <div class="sidebar-stat"><div class="s-label">NET UPTIME</div><div class="s-value" style="color:{clr};">{kpis['uptime']}%</div></div>
        <div class="sidebar-stat"><div class="s-label">NODES ONLINE</div><div class="s-value" style="color:{clr};">{kpis['nodes_online']} / 3</div></div>
        <div class="sidebar-stat"><div class="s-label">CO₂ AVOIDED</div><div class="s-value">{kpis['co2_saved']} kg</div></div>
        <div class="sidebar-stat">
          <div class="s-label">TRAFFIC DENSITY <span style="font-size:.55rem;color:{'#00ff88' if trf['source']=='opencv' else '#64748b'};">● {'OPENCV' if trf['source']=='opencv' else 'NO CAM'}</span></div>
          <div class="s-value" style="color:{trf_clr};">{trf['density']:.0f}%</div>
          <div class="traffic-bar-wrap"><div class="traffic-bar" style="width:{trf['density']}%;background:{trf_clr};"></div></div>
        </div>
        <div class="sidebar-stat">
          <div class="s-label">BACKEND</div>
          <div class="s-value" style="color:{'#00ff88' if be_ok else '#ff3366'};">{'● ONLINE' if be_ok else '○ OFFLINE'}</div>
        </div>
        <div class="sidebar-stat">
          <div class="s-label">SOCKET</div>
          <div class="s-value" style="color:{'#00ff88' if sock_ok else '#ffd700'};">{'● LIVE' if sock_ok else '◑ POLLING'}</div>
        </div>
        """,unsafe_allow_html=True)
    sidebar_live()

    st.markdown(f'<div style="font-family:Share Tech Mono;font-size:.6rem;color:#334155;text-align:center;padding:.4rem 0;">{st.session_state.username} · {st.session_state.role}</div>',unsafe_allow_html=True)
    if st.button("🚪 Logout",use_container_width=True):
        st.session_state.logged_in=False; st.rerun()

# ══════════════════════════════════════════════════════════════
#  PAGES
# ══════════════════════════════════════════════════════════════

@st.fragment(run_every=2)
def page_overview():
    nodes=get_node_data(); kpis=get_kpis(nodes)
    trf=st.session_state.traffic_data
    be_ok=st.session_state.backend_online

    ct,ctk=st.columns([3,1])
    with ct: st.markdown('<div class="page-title-block"><h2>🌐 Global Overview</h2><div class="subtitle">REAL-TIME ENERGY MANAGEMENT · ESP32 + OPENCV</div></div>',unsafe_allow_html=True)
    with ctk:
        sock_clr="#00ff88" if st.session_state.socket_connected else "#ffd700"
        conn_txt="LIVE" if st.session_state.socket_connected else "POLLING"
        st.markdown(f'<div style="text-align:right;margin-top:.5rem;"><div class="live-ticker"><span class="pulse-dot"></span>&nbsp;{datetime.now().strftime("%H:%M:%S")}</div><div style="font-family:Share Tech Mono;font-size:.6rem;color:{sock_clr};margin-top:.3rem;">● {conn_txt}</div></div>',unsafe_allow_html=True)

    if not be_ok:
        st.warning("⚠️ Backend offline — start `python backend.py` then refresh. Showing last known state.")

    k1,k2,k3,k4,k5,k6=st.columns(6)
    cards=[
        (k1,"Energy Saved",f"{kpis['energy_saved']}%","green","↑ vs traditional"),
        (k2,"Traffic Density",f"{trf['density']:.0f}%","yellow" if trf['density']>50 else "cyan",f"{trf['count']} vehicles"),
        (k3,"Active Faults",str(kpis['faults']),"red" if kpis['faults']>0 else "green","⚠ CRITICAL" if kpis['faults']>0 else "✓ Clear"),
        (k4,"Net Uptime",f"{kpis['uptime']}%","cyan","30-day avg"),
        (k5,"CO₂ Avoided",f"{kpis['co2_saved']} kg","green","today"),
        (k6,"Avg Brightness",f"{kpis['avg_brightness']}%","yellow","across nodes"),
    ]
    for col,label,val,color,delta in cards:
        with col: st.markdown(f'<div class="kpi-card {color}"><div class="kpi-label">{label}</div><div class="kpi-value {color}">{val}</div><div class="kpi-delta" style="color:var(--text-muted);">{delta}</div></div>',unsafe_allow_html=True)

    st.markdown('<div class="section-header"><h3>⚡ 24-Hour Energy Comparison</h3></div>',unsafe_allow_html=True)
    df=get_energy_df(kpis["nodes_online"])
    fig=go.Figure()
    fig.add_trace(go.Scatter(x=df["timestamp"],y=df["traditional"],name="Traditional",line=dict(color="#ff6b35",width=2,dash="dot")))
    fig.add_trace(go.Scatter(x=df["timestamp"],y=df["luminet"],name="LumiNet AI",line=dict(color="#00f5ff",width=2.5),fill="tozeroy",fillcolor="rgba(0,245,255,0.06)"))
    fig.update_layout(**PLOTLY,height=300,margin=dict(l=30,r=20,t=20,b=30),legend=dict(orientation="h",y=-0.25))
    st.markdown('<div class="chart-container">',unsafe_allow_html=True); st.plotly_chart(fig,use_container_width=True); st.markdown('</div>',unsafe_allow_html=True)

    st.markdown('<div class="section-header"><h3>📡 Node Status</h3></div>',unsafe_allow_html=True)
    cols=st.columns(3)
    for i,n in enumerate(nodes):
        src_cls="wifi" if n["source"]=="mqtt" else ("serial" if n["source"]=="serial" else "offline")
        src_lbl="WiFi" if n["source"]=="mqtt" else ("Serial" if n["source"]=="serial" else "Offline")
        with cols[i]:
            st.markdown(f"""
            <div class="node-card {n['css_class']}">
              <div style="font-family:Orbitron;font-weight:700;color:{n['color']};">
                {n['node_id']}<span class="source-badge {src_cls}">{src_lbl}</span>
              </div>
              <div style="font-size:1.6rem;margin:6px 0;">{n['icon']}</div>
              <div style="font-family:Orbitron;font-size:1.1rem;color:{n['color']};">{n['brightness']:.0f}%</div>
              <div style="font-size:.65rem;color:#64748b;font-family:Share Tech Mono;">
                {n['status']} · {n['voltage']:.0f}V · Ambient {n['ambient']}%
              </div>
              <div style="font-size:.6rem;color:#64748b;font-family:Share Tech Mono;">
                Traffic {n['traffic']:.0f}% · Uptime {n['uptime_h']}h
              </div>
            </div>""",unsafe_allow_html=True)

    st.markdown('<div class="cyber-divider"></div><div class="section-header"><h3>📥 Export</h3></div>',unsafe_allow_html=True)
    ec1,ec2=st.columns(2)
    with ec1:
        st.download_button("⬇ CSV",df.to_csv(index=False).encode(),"luminet_energy.csv","text/csv",use_container_width=True)
    with ec2:
        if PDF_OK:
            buf=io.BytesIO()
            doc=SimpleDocTemplate(buf,pagesize=letter)
            styles=getSampleStyleSheet()
            elems=[Paragraph("LumiNet AI Report",styles["Title"]),Spacer(1,12),
                   Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",styles["Normal"]),Spacer(1,12)]
            tbl_data=[["Node","Status","Brightness","Voltage","Source"]] + \
                     [[n["node_id"],n["status"],f"{n['brightness']:.0f}%",f"{n['voltage']:.0f}V",n["source"]] for n in nodes]
            t=Table(tbl_data)
            t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,0),rl_colors.HexColor("#003355")),
                                   ("TEXTCOLOR",(0,0),(-1,0),rl_colors.white),
                                   ("GRID",(0,0),(-1,-1),.5,rl_colors.grey)]))
            elems.append(t)
            doc.build(elems)
            st.download_button("⬇ PDF",buf.getvalue(),"luminet_report.pdf","application/pdf",use_container_width=True)
        else:
            st.info("pip install reportlab to enable PDF")


@st.fragment(run_every=2)
def page_map():
    nodes=get_node_data()
    st.markdown('<div class="page-title-block"><h2>🗺️ Node Cluster Map</h2><div class="subtitle">GIS REAL-TIME TELEMETRY · CLUSTER ALPHA</div></div>',unsafe_allow_html=True)

    cols=st.columns(3)
    for i,n in enumerate(nodes):
        src_cls="wifi" if n["source"]=="mqtt" else ("serial" if n["source"]=="serial" else "offline")
        src_lbl="WiFi" if n["source"]=="mqtt" else ("Serial" if n["source"]=="serial" else "Offline")
        with cols[i]:
            st.markdown(f"""
            <div class="node-card {n['css_class']}">
              <div style="font-family:Orbitron;font-weight:700;color:{n['color']};">{n['node_id']}<span class="source-badge {src_cls}">{src_lbl}</span></div>
              <div style="font-size:1.8rem;margin:6px 0;">{n['icon']}</div>
              <div style="font-family:Orbitron;font-size:1.3rem;color:{n['color']};">{n['brightness']:.0f}%</div>
              <div style="font-size:.65rem;color:#64748b;font-family:Share Tech Mono;">{n['status']} · {n['voltage']:.0f}V · {n['uptime_h']}h</div>
              <div style="font-size:.6rem;color:#64748b;font-family:Share Tech Mono;">LDR: {n['ambient']}% · Traffic: {n['traffic']:.0f}%</div>
            </div>""",unsafe_allow_html=True)

    df_n=pd.DataFrame(nodes)
    fig=go.Figure(go.Scattermapbox(
        lat=df_n["lat"],lon=df_n["lon"],mode="markers+text",
        marker=dict(size=20,color=df_n["color"]),
        text=df_n["node_id"],textposition="top right",textfont=dict(size=13,color="white"),
        customdata=np.stack([df_n["status"],df_n["brightness"].round(0),df_n["voltage"].round(0),df_n["source"]],axis=-1),
        hovertemplate="<b>%{text}</b><br>Status: %{customdata[0]}<br>Brightness: %{customdata[1]}%<br>Voltage: %{customdata[2]}V<br>Source: %{customdata[3]}<extra></extra>"
    ))
    fig.update_layout(mapbox=dict(style="open-street-map",center=dict(lat=19.87604,lon=75.3416),zoom=17.5),
                      margin=dict(l=0,r=0,t=0,b=0),height=460)
    st.markdown('<div class="chart-container" style="padding:0;">',unsafe_allow_html=True)
    st.plotly_chart(fig,use_container_width=True)
    st.markdown('</div>',unsafe_allow_html=True)

    if st.session_state.role=="Admin":
        st.markdown('<div class="section-header"><h3>🎛️ Manual Brightness Override → ESP32</h3></div>',unsafe_allow_html=True)
        sc=st.columns(3)
        for i,n in enumerate(nodes):
            with sc[i]:
                cur=st.session_state.node_overrides.get(n["node_id"],int(n["brightness"]))
                val=st.slider(n["node_id"],0,100,cur,key=f"sl_{n['node_id']}")
                if val!=cur:
                    st.session_state.node_overrides[n["node_id"]]=val
                    send_manual_brightness(n["node_id"],val)
        if st.button("🔄 Reset All Overrides"):
            st.session_state.node_overrides={}
            for n in nodes: send_manual_brightness(n["node_id"],-1)
            st.rerun()


@st.fragment(run_every=2)
def page_faults():
    nodes=get_node_data(); kpis=get_kpis(nodes)
    fault_nodes=[n for n in nodes if n["status"]=="Fault"]
    st.markdown('<div class="page-title-block"><h2>🔴 Fault Diagnostics</h2><div class="subtitle">LIVE ALERT FEED · AUTO-REFRESH 2s</div></div>',unsafe_allow_html=True)
    f1,f2,f3=st.columns(3)
    with f1: st.markdown(f'<div class="kpi-card red"><div class="kpi-label">Active Faults</div><div class="kpi-value red">{kpis["faults"]}</div></div>',unsafe_allow_html=True)
    with f2: st.markdown(f'<div class="kpi-card {"green" if kpis["uptime"]>=99 else "orange"}"><div class="kpi-label">Uptime</div><div class="kpi-value {"green" if kpis["uptime"]>=99 else "orange"}">{kpis["uptime"]}%</div></div>',unsafe_allow_html=True)
    with f3: st.markdown(f'<div class="kpi-card cyan"><div class="kpi-label">Nodes Online</div><div class="kpi-value cyan">{kpis["nodes_online"]} / 3</div></div>',unsafe_allow_html=True)
    st.markdown('<div class="section-header"><h3>⚡ Active Faults</h3></div>',unsafe_allow_html=True)
    if not fault_nodes:
        st.success("✅ All systems nominal.")
    else:
        for n in fault_nodes:
            st.markdown(f'<div class="alert-item"><b>{datetime.now().strftime("%H:%M:%S")} · {n["node_id"]}</b> &nbsp;<span style="color:var(--accent-red);font-weight:700;">FAULT — Low Voltage {n["voltage"]:.0f}V</span><br>Ambient: {n["ambient"]}% · Brightness: {n["brightness"]:.0f}% · Uptime: {n["uptime_h"]}h · Source: {n["source"]}</div>',unsafe_allow_html=True)

    recent=[a for a in st.session_state.alert_log if a["level"]=="critical"][:10]
    if recent:
        st.markdown('<div class="section-header"><h3>📋 Recent Fault Log</h3></div>',unsafe_allow_html=True)
        st.dataframe(pd.DataFrame(recent),use_container_width=True,hide_index=True)


def page_tasks():
    st.markdown('<div class="page-title-block"><h2>📋 Task Management</h2><div class="subtitle">WORKFORCE ASSIGNMENT & TRACKING</div></div>',unsafe_allow_html=True)
    role=st.session_state.role; tasks=st.session_state.tasks_db
    pending=sum(1 for t in tasks if t["status"]=="Pending")
    completed=sum(1 for t in tasks if t["status"]=="Completed")
    critical=sum(1 for t in tasks if t["urgency"]=="CRITICAL" and t["status"]=="Pending")
    c1,c2,c3=st.columns(3)
    with c1: st.markdown(f'<div class="kpi-card orange"><div class="kpi-label">Pending</div><div class="kpi-value orange">{pending}</div></div>',unsafe_allow_html=True)
    with c2: st.markdown(f'<div class="kpi-card green"><div class="kpi-label">Completed</div><div class="kpi-value green">{completed}</div></div>',unsafe_allow_html=True)
    with c3: st.markdown(f'<div class="kpi-card red"><div class="kpi-label">Critical Pending</div><div class="kpi-value red">{critical}</div></div>',unsafe_allow_html=True)
    if role=="Admin":
        st.markdown('<div class="section-header"><h3>➕ Assign New Task</h3></div>',unsafe_allow_html=True)
        with st.form("new_task_form",clear_on_submit=True):
            fc1,fc2,fc3=st.columns([1,2,1])
            with fc1: n_id=st.selectbox("Node",NODE_IDS); urgency=st.selectbox("Urgency",["WARNING","CRITICAL","INFO"])
            with fc2: desc=st.text_input("Description",placeholder="Describe the maintenance task…")
            with fc3: assigned=st.selectbox("Assign To",[u["username"] for u in st.session_state.user_db if u["role"]=="Technician"])
            if st.form_submit_button("🚀 Deploy",use_container_width=True):
                if desc.strip():
                    new_id=max(t["id"] for t in tasks)+1 if tasks else 1000
                    st.session_state.tasks_db.insert(0,{"id":new_id,"node":n_id,"urgency":urgency,"desc":desc.strip(),"assigned_to":assigned,"status":"Pending","time":datetime.now().strftime("%Y-%m-%d %H:%M")})
                    push_local_alert(f"Task #{new_id} deployed on {n_id}","info"); st.rerun()
                else: st.error("Description required.")
    st.markdown('<div class="section-header"><h3>📄 Task Log</h3></div>',unsafe_allow_html=True)
    sf1,sf2=st.columns(2)
    with sf1: sf=st.selectbox("Status",["All","Pending","Completed"])
    with sf2: uf=st.selectbox("Urgency",["All","CRITICAL","WARNING","INFO"])
    filtered=[t for t in tasks if (sf=="All" or t["status"]==sf) and (uf=="All" or t["urgency"]==uf)]
    if role=="Technician": filtered=[t for t in filtered if t["assigned_to"]==st.session_state.username]
    for task in filtered:
        css="completed" if task["status"]=="Completed" else task["urgency"].lower()
        st.markdown(f'<div class="task-card {css}"><span style="font-family:Orbitron;font-size:.8rem;font-weight:700;">Task #{task["id"]} — {task["node"]}</span>&nbsp;<span style="font-family:Share Tech Mono;font-size:.65rem;color:var(--text-muted);">{task["time"]}</span><br><span style="font-size:.85rem;">{task["desc"]}</span><br><span style="font-family:Share Tech Mono;font-size:.65rem;color:var(--text-muted);">Assigned: {task["assigned_to"]} · {task["status"]} · {task["urgency"]}</span></div>',unsafe_allow_html=True)
        if role in ["Technician","Admin"] and task["status"]=="Pending":
            if st.button(f"✅ Complete #{task['id']}",key=f"done_{task['id']}"):
                task["status"]="Completed"; push_local_alert(f"Task #{task['id']} completed","info"); st.rerun()
        if role=="Admin":
            if st.button(f"🗑 Delete #{task['id']}",key=f"del_{task['id']}"):
                st.session_state.tasks_db=[t for t in st.session_state.tasks_db if t["id"]!=task["id"]]; st.rerun()


@st.fragment(run_every=3)
def page_ai():
    nodes=get_node_data()
    st.markdown('<div class="page-title-block"><h2>🧠 AI Analytics</h2><div class="subtitle">NEURAL BRIGHTNESS OPTIMIZATION · LIVE ESP32 DATA</div></div>',unsafe_allow_html=True)
    st.markdown('<div class="chart-container" style="text-align:center;font-family:Share Tech Mono;color:var(--accent-cyan);font-size:.9rem;padding:1.2rem;"><b>Brightness</b> = (0.4 × Ambient) + (0.3 × Traffic) + (0.2 × Road Priority) + (0.1 × Distance)</div><br>',unsafe_allow_html=True)

    T,A=np.meshgrid(np.linspace(0,100,40),np.linspace(0,100,40))
    B=np.clip(0.4*A+0.3*T+18,0,100)
    fig3d=go.Figure(go.Surface(z=B,x=T,y=A,colorscale="Viridis",opacity=0.85,
                                contours=dict(z=dict(show=True,usecolormap=True,project_z=True,width=2))))
    fig3d.update_layout(**PLOTLY,height=480,margin=dict(l=0,r=0,t=30,b=0),
                        scene=dict(xaxis=dict(title="Traffic Density (%)",gridcolor="rgba(0,245,255,.1)",backgroundcolor="rgba(0,0,0,0)"),
                                   yaxis=dict(title="Ambient Light (%)",gridcolor="rgba(0,245,255,.1)",backgroundcolor="rgba(0,0,0,0)"),
                                   zaxis=dict(title="Brightness (%)",gridcolor="rgba(0,245,255,.1)",backgroundcolor="rgba(0,0,0,0)"),
                                   bgcolor="rgba(0,0,0,0)"),
                        title=dict(text="Brightness Response Surface",font=dict(family="Orbitron",color="#00f5ff",size=13),x=0.5))
    st.plotly_chart(fig3d,use_container_width=True)

    rows=[]
    for n in nodes:
        pred=round(np.clip(0.4*n["ambient"]+0.3*n["traffic"]+18,0,100),1)
        rows.append({"Node":n["node_id"],"Ambient":n["ambient"],"Traffic":f"{n['traffic']:.0f}%",
                     "Actual":f"{n['brightness']:.0f}%","Predicted":f"{pred}%",
                     "Delta":round(n["brightness"]-pred,1),"Source":n["source"]})
    st.markdown('<div class="section-header"><h3>📊 Live Node Predictions</h3></div>',unsafe_allow_html=True)
    st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)


def page_historical():
    st.markdown('<div class="page-title-block"><h2>📈 Historical Trends</h2><div class="subtitle">7-DAY ENERGY & SAVINGS ANALYSIS</div></div>',unsafe_allow_html=True)
    df=st.session_state.hist_data.copy()
    d1,d2=st.columns(2)
    with d1: s=st.date_input("From",value=df["timestamp"].min().date())
    with d2: e=st.date_input("To",value=df["timestamp"].max().date())
    df=df[(df["timestamp"].dt.date>=s)&(df["timestamp"].dt.date<=e)]
    k1,k2,k3,k4=st.columns(4)
    with k1: st.markdown(f'<div class="kpi-card green"><div class="kpi-label">Avg Savings</div><div class="kpi-value green">{df["savings_pct"].mean():.1f}%</div></div>',unsafe_allow_html=True)
    with k2: st.markdown(f'<div class="kpi-card cyan"><div class="kpi-label">Total CO₂ Saved</div><div class="kpi-value cyan">{df["co2_saved"].sum():.2f} t</div></div>',unsafe_allow_html=True)
    with k3: st.markdown(f'<div class="kpi-card orange"><div class="kpi-label">Peak Traditional</div><div class="kpi-value orange">{df["traditional"].max():.0f} W</div></div>',unsafe_allow_html=True)
    with k4: st.markdown(f'<div class="kpi-card green"><div class="kpi-label">Lowest LumiNet</div><div class="kpi-value green">{df["luminet"].min():.0f} W</div></div>',unsafe_allow_html=True)

    for title, fn in [("⚡ Energy Consumption",lambda: _plot_energy(df)),
                       ("🌿 Savings %",lambda: _plot_savings(df)),
                       ("🌡️ Heatmap",lambda: _plot_heatmap(df))]:
        st.markdown(f'<div class="section-header"><h3>{title}</h3></div>',unsafe_allow_html=True)
        fn()

    st.download_button("⬇ Export CSV",df.to_csv(index=False).encode(),"luminet_historical.csv","text/csv",use_container_width=True)

def _plot_energy(df):
    fig=go.Figure()
    fig.add_trace(go.Scatter(x=df["timestamp"],y=df["traditional"],name="Traditional",line=dict(color="#ff6b35",width=1.5,dash="dot")))
    fig.add_trace(go.Scatter(x=df["timestamp"],y=df["luminet"],name="LumiNet AI",line=dict(color="#00f5ff",width=2),fill="tozeroy",fillcolor="rgba(0,245,255,.06)"))
    fig.update_layout(**PLOTLY,height=280,margin=dict(l=30,r=20,t=20,b=30),legend=dict(orientation="h",y=-0.3))
    st.markdown('<div class="chart-container">',unsafe_allow_html=True); st.plotly_chart(fig,use_container_width=True); st.markdown('</div>',unsafe_allow_html=True)

def _plot_savings(df):
    fig=go.Figure(go.Scatter(x=df["timestamp"],y=df["savings_pct"],line=dict(color="#00ff88",width=2),fill="tozeroy",fillcolor="rgba(0,255,136,.06)"))
    fig.update_layout(**PLOTLY,height=220,margin=dict(l=30,r=20,t=20,b=30))
    st.markdown('<div class="chart-container">',unsafe_allow_html=True); st.plotly_chart(fig,use_container_width=True); st.markdown('</div>',unsafe_allow_html=True)

def _plot_heatmap(df):
    df2=df.copy(); df2["hour"]=df2["timestamp"].dt.hour; df2["day"]=df2["timestamp"].dt.strftime("%a %m/%d")
    pivot=df2.pivot_table(values="savings_pct",index="hour",columns="day",aggfunc="mean")
    fig=px.imshow(pivot,color_continuous_scale="Viridis",labels=dict(x="Day",y="Hour",color="Savings %"),aspect="auto")
    fig.update_layout(**PLOTLY,height=320,margin=dict(l=50,r=20,t=20,b=30))
    st.markdown('<div class="chart-container">',unsafe_allow_html=True); st.plotly_chart(fig,use_container_width=True); st.markdown('</div>',unsafe_allow_html=True)


def page_alerts():
    st.markdown('<div class="page-title-block"><h2>🔔 Alert Center</h2><div class="subtitle">SYSTEM NOTIFICATIONS · AUTO-PUSHED FROM ESP32</div></div>',unsafe_allow_html=True)
    alerts=st.session_state.alert_log
    c1,c2,c3=st.columns(3)
    with c1: st.markdown(f'<div class="kpi-card red"><div class="kpi-label">Critical</div><div class="kpi-value red">{sum(1 for a in alerts if a["level"]=="critical")}</div></div>',unsafe_allow_html=True)
    with c2: st.markdown(f'<div class="kpi-card yellow"><div class="kpi-label">Warnings</div><div class="kpi-value yellow">{sum(1 for a in alerts if a["level"]=="warn")}</div></div>',unsafe_allow_html=True)
    with c3: st.markdown(f'<div class="kpi-card cyan"><div class="kpi-label">Info</div><div class="kpi-value cyan">{sum(1 for a in alerts if a["level"]=="info")}</div></div>',unsafe_allow_html=True)
    ac1,ac2=st.columns([3,1])
    with ac1: cm=st.text_input("Custom alert",placeholder="Enter message…",label_visibility="collapsed")
    with ac2:
        if st.button("🗑 Clear",use_container_width=True): st.session_state.alert_log=[]; st.rerun()
    if cm and st.button("🔔 Push"): push_local_alert(cm,"warn"); st.rerun()
    st.markdown('<div class="section-header"><h3>📋 Feed</h3></div>',unsafe_allow_html=True)
    if not alerts: st.info("No alerts yet.")
    for a in alerts:
        st.markdown(f'<div class="alert-item {a["level"]}"><b>{a["time"]}</b>&nbsp;&nbsp;{a["msg"]}</div>',unsafe_allow_html=True)


def page_users():
    st.markdown('<div class="page-title-block"><h2>👥 User Management</h2><div class="subtitle">ACCESS CONTROL & ROLE ASSIGNMENT</div></div>',unsafe_allow_html=True)
    users=st.session_state.user_db
    k1,k2,k3=st.columns(3)
    with k1: st.markdown(f'<div class="kpi-card cyan"><div class="kpi-label">Total</div><div class="kpi-value cyan">{len(users)}</div></div>',unsafe_allow_html=True)
    with k2: st.markdown(f'<div class="kpi-card green"><div class="kpi-label">Active</div><div class="kpi-value green">{sum(1 for u in users if u["active"])}</div></div>',unsafe_allow_html=True)
    with k3: st.markdown(f'<div class="kpi-card orange"><div class="kpi-label">Inactive</div><div class="kpi-value orange">{sum(1 for u in users if not u["active"])}</div></div>',unsafe_allow_html=True)
    st.markdown('<div class="section-header"><h3>➕ Add User</h3></div>',unsafe_allow_html=True)
    with st.form("add_user",clear_on_submit=True):
        uc1,uc2,uc3=st.columns(3)
        with uc1: nu=st.text_input("Username",placeholder="username")
        with uc2: np_=st.text_input("Password",placeholder="password")
        with uc3: nr=st.selectbox("Role",["Admin","Technician","Viewer"])
        if st.form_submit_button("➕ Add",use_container_width=True):
            if nu.strip() and np_.strip():
                if any(u["username"]==nu for u in users): st.error("Username exists.")
                else:
                    st.session_state.user_db.append({"username":nu,"password":np_,"role":nr,"active":True})
                    push_local_alert(f"User '{nu}' added","info"); st.rerun()
            else: st.error("Fill all fields.")
    st.markdown('<div class="section-header"><h3>📋 Directory</h3></div>',unsafe_allow_html=True)
    for u in users:
        rc=u["role"].lower(); ac="#00ff88" if u["active"] else "#ff3366"; al="ACTIVE" if u["active"] else "INACTIVE"
        st.markdown(f'<div class="user-row"><span style="font-family:Orbitron;font-weight:700;min-width:120px;">{u["username"]}</span><span class="role-badge {rc}">{u["role"]}</span><span style="font-family:Share Tech Mono;font-size:.65rem;color:{ac};margin-left:auto;">● {al}</span></div>',unsafe_allow_html=True)
        if u["username"]!=st.session_state.username:
            bc1,bc2=st.columns(2)
            with bc1:
                if st.button("🔒 Deactivate" if u["active"] else "🔓 Activate",key=f"tog_{u['username']}",use_container_width=True):
                    u["active"]=not u["active"]; push_local_alert(f"User '{u['username']}' {'activated' if u['active'] else 'deactivated'}","warn"); st.rerun()
            with bc2:
                if st.button("🗑 Remove",key=f"del_{u['username']}",use_container_width=True):
                    st.session_state.user_db=[x for x in st.session_state.user_db if x["username"]!=u["username"]]; push_local_alert(f"User '{u['username']}' removed","warn"); st.rerun()

# ══════════════════════════════════════════════════════════════
#  ROUTER
# ══════════════════════════════════════════════════════════════
p=st.session_state.page
if   p=="🌐 Global Overview":   page_overview()
elif p=="🗺️ Node Cluster Map":  page_map()
elif p=="🔴 Fault Diagnostics": page_faults()
elif p=="📋 Task Management":   page_tasks()
elif p=="🧠 AI Analytics":      page_ai()
elif p=="📈 Historical Trends": page_historical()
elif p=="🔔 Alert Center":      page_alerts()
elif p=="👥 User Management":   page_users()
