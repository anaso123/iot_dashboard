import os
import time
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from influxdb_client import InfluxDBClient
from plotly.subplots import make_subplots

# ---------------------------
# CONFIG (usa st.secrets o variables de entorno, NO pongas el token en el código)
# .streamlit/secrets.toml:
#   INFLUX_URL   = "https://us-east-1-1.aws.cloud2.influxdata.com"
#   INFLUX_TOKEN = "tu_token"
#   INFLUX_ORG   = "miguelcmo"
#   INFLUX_BUCKET = "iot_telemetry_data"
# ---------------------------
def get_secret(key, default=""):
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, default)


URL = get_secret("INFLUX_URL", "https://us-east-1-1.aws.cloud2.influxdata.com")
TOKEN = get_secret("INFLUX_TOKEN", "")
ORG = get_secret("INFLUX_ORG", "miguelcmo")
BUCKET = get_secret("INFLUX_BUCKET", "iot_telemetry_data")

VARS_LABEL = {"temperature": "🌡️ Temperatura (°C)", "humidity": "💧 Humedad (%)"}
COLORS = {"temperature": "#e4572e", "humidity": "#2e86e4"}

# ---------------------------
# STREAMLIT UI
# ---------------------------
st.set_page_config(page_title="IoT Dashboard", layout="wide", page_icon="🌡️")

st.title("🌡️ IoT Telemetry Dashboard")
st.markdown("Monitoreo interactivo de temperatura y humedad")

if not TOKEN:
    st.error("Falta el token de InfluxDB. Configúralo en `st.secrets['INFLUX_TOKEN']` o como variable de entorno.")
    st.stop()

# ---------------------------
# SIDEBAR
# ---------------------------
st.sidebar.header("⚙️ Configuración")

# --- Rango de tiempo
st.sidebar.subheader("Rango de tiempo")
preset = st.sidebar.selectbox(
    "Periodo",
    ["-1h", "-6h", "-12h", "-24h", "-7d", "-30d", "Personalizado"],
    index=3,
)

range_start, range_stop = preset, None
if preset == "Personalizado":
    d_from = st.sidebar.date_input("Desde", value=date.today() - timedelta(days=1))
    d_to = st.sidebar.date_input("Hasta", value=date.today())
    if d_from > d_to:
        st.sidebar.error("La fecha 'Desde' debe ser anterior a 'Hasta'.")
        st.stop()
    range_start = f"{d_from.isoformat()}T00:00:00Z"
    range_stop = f"{(d_to + timedelta(days=1)).isoformat()}T00:00:00Z"

tz = st.sidebar.selectbox(
    "Zona horaria",
    ["America/Bogota", "UTC", "America/Mexico_City", "America/Lima", "America/Santiago", "Europe/Madrid"],
)

# --- Filtros
st.sidebar.subheader("Datos")
variables = st.sidebar.multiselect(
    "Variables a mostrar",
    options=list(VARS_LABEL.keys()),
    default=list(VARS_LABEL.keys()),
    format_func=lambda v: VARS_LABEL[v],
)
tag_name = st.sidebar.text_input(
    "Tag de dispositivo (opcional)",
    value="",
    help="Si tus datos tienen un tag (ej. device, sensor_id) escríbelo para poder filtrar por dispositivo.",
)

# --- Visualización
st.sidebar.subheader("Visualización")
show_raw = st.sidebar.checkbox("Mostrar datos originales", value=True)
show_rolling = st.sidebar.checkbox("Mostrar media móvil", value=True)
rolling_window = st.sidebar.slider("Ventana media móvil (puntos)", 2, 200, 20, disabled=not show_rolling)
show_thresholds = st.sidebar.checkbox("Mostrar zona de confort/umbrales", value=True)
show_anomalies = st.sidebar.checkbox("Marcar anomalías", value=True)

# --- Alertas
with st.sidebar.expander("🚨 Umbrales y alertas", expanded=False):
    temp_range = st.slider("Rango aceptable temperatura (°C)", -10.0, 60.0, (18.0, 30.0), 0.5)
    hum_range = st.slider("Rango aceptable humedad (%)", 0.0, 100.0, (30.0, 70.0), 1.0)
    z_thr = st.slider("Sensibilidad anomalías (z-score)", 1.5, 5.0, 3.0, 0.1)

# --- Remuestreo
st.sidebar.subheader("Remuestreo")
resample_freq = st.sidebar.selectbox("Intervalo", ["1min", "5min", "10min", "30min", "1h", "6h"], index=2)
agg_label = st.sidebar.selectbox("Agregación", ["Promedio", "Mediana", "Mínimo", "Máximo"])
AGG_MAP = {"Promedio": "mean", "Mediana": "median", "Mínimo": "min", "Máximo": "max"}

# --- Auto-refresh
st.sidebar.subheader("🔄 Actualización")
auto_refresh = st.sidebar.toggle("Auto-refresh", value=False)
refresh = st.sidebar.slider("Cada (segundos)", 5, 120, 15, disabled=not auto_refresh)
if st.sidebar.button("Refrescar ahora", use_container_width=True):
    st.cache_data.clear()
    st.rerun()


# ---------------------------
# DATA FUNCTION
# ---------------------------
@st.cache_data(ttl=5, show_spinner="Consultando InfluxDB...")
def load_data(start, stop, tag):
    tag = "".join(c for c in tag if c.isalnum() or c == "_")  # sanitiza el nombre del tag
    range_clause = f"start: {start}" + (f", stop: {stop}" if stop else "")
    row_keys = '["_time"' + (f', "{tag}"' if tag else "") + "]"
    keep_cols = '["_time", "temperature", "humidity"' + (f', "{tag}"' if tag else "") + "]"

    query = f'''
    from(bucket: "{BUCKET}")
      |> range({range_clause})
      |> filter(fn: (r) => r["_field"] == "temperature" or r["_field"] == "humidity")
      |> pivot(rowKey: {row_keys}, columnKey: ["_field"], valueColumn: "_value")
      |> keep(columns: {keep_cols})
    '''

    with InfluxDBClient(url=URL, token=TOKEN, org=ORG, timeout=30_000) as client:
        result = client.query_api().query_data_frame(query)

    if isinstance(result, list):
        result = [r for r in result if not r.empty]
        if not result:
            return pd.DataFrame()
        df = pd.concat(result, ignore_index=True)
    else:
        df = result

    if df.empty:
        return df

    for col in ["temperature", "humidity"]:
        if col not in df.columns:
            df[col] = float("nan")
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["_time"] = pd.to_datetime(df["_time"], utc=True)
    return df.sort_values("_time").reset_index(drop=True)


try:
    df = load_data(range_start, range_stop, tag_name)
except Exception as e:
    st.error(f"No se pudo consultar InfluxDB: {e}")
    st.stop()

if df.empty:
    st.warning("No hay datos disponibles en este rango de tiempo")
    st.stop()

df = df.copy()
df["_time"] = df["_time"].dt.tz_convert(tz)

# Filtro por dispositivo (si existe el tag)
clean_tag = "".join(c for c in tag_name if c.isalnum() or c == "_")
if clean_tag and clean_tag in df.columns:
    devices = sorted(df[clean_tag].dropna().astype(str).unique())
    chosen = st.sidebar.multiselect(f"Filtrar por {clean_tag}", devices, default=devices)
    df = df[df[clean_tag].astype(str).isin(chosen)]

if df.empty or not variables:
    st.warning("Selecciona al menos una variable y/o dispositivo con datos.")
    st.stop()

# Calculos auxiliares
for v in variables:
    df[f"{v}_roll"] = df[v].rolling(rolling_window, min_periods=1).mean()
    std = df[v].std()
    df[f"{v}_z"] = (df[v] - df[v].mean()) / std if std and std > 0 else 0.0
    df[f"{v}_anom"] = df[f"{v}_z"].abs() > z_thr

LIMITS = {"temperature": temp_range, "humidity": hum_range}
GAUGE_AXIS = {"temperature": (-10, 60), "humidity": (0, 100)}
UNITS = {"temperature": "°C", "humidity": "%"}


def last_value(col):
    s = df[col].dropna()
    return s.iloc[-1] if not s.empty else None


# ---------------------------
# TABS
# ---------------------------
tab_resumen, tab_series, tab_dist, tab_patrones, tab_datos = st.tabs(
    ["📊 Resumen", "📈 Series de tiempo", "📉 Distribución", "🔍 Patrones y correlación", "📋 Datos"]
)

# ===== RESUMEN =====
with tab_resumen:
    cols = st.columns(len(variables) * 2)
    i = 0
    for v in variables:
        cur = last_value(v)
        prev = df[v].dropna().iloc[-2] if df[v].dropna().shape[0] > 1 else None
        delta = (cur - prev) if (cur is not None and prev is not None) else None
        cols[i].metric(
            f"{VARS_LABEL[v]} actual",
            f"{cur:.2f} {UNITS[v]}" if cur is not None else "—",
            f"{delta:+.2f}" if delta is not None else None,
        )
        cols[i + 1].metric(
            f"Promedio / Mín / Máx",
            f"{df[v].mean():.2f} {UNITS[v]}",
            f"{df[v].min():.1f} / {df[v].max():.1f}",
            delta_color="off",
        )
        i += 2

    # Alertas
    st.subheader("🚨 Estado de alertas")
    alert_cols = st.columns(len(variables))
    for c, v in zip(alert_cols, variables):
        lo, hi = LIMITS[v]
        cur = last_value(v)
        out_pct = ((df[v] < lo) | (df[v] > hi)).mean() * 100
        with c:
            if cur is None:
                st.info(f"{VARS_LABEL[v]}: sin datos")
            elif cur < lo or cur > hi:
                st.error(f"{VARS_LABEL[v]}: valor actual **fuera de rango** ({lo}–{hi})")
            else:
                st.success(f"{VARS_LABEL[v]}: dentro de rango ({lo}–{hi})")
            st.caption(f"{out_pct:.1f}% de las lecturas fuera de rango · "
                       f"{int(df[f'{v}_anom'].sum())} anomalías (|z| > {z_thr})")

    # Gauges
    st.subheader("🎛️ Medidores")
    gcols = st.columns(len(variables))
    for c, v in zip(gcols, variables):
        cur = last_value(v)
        if cur is None:
            continue
        lo, hi = LIMITS[v]
        gmin, gmax = GAUGE_AXIS[v]
        fig_g = go.Figure(
            go.Indicator(
                mode="gauge+number+delta",
                value=cur,
                delta={"reference": df[v].mean(), "valueformat": ".2f"},
                title={"text": VARS_LABEL[v]},
                number={"suffix": f" {UNITS[v]}"},
                gauge={
                    "axis": {"range": [gmin, gmax]},
                    "bar": {"color": COLORS[v]},
                    "steps": [
                        {"range": [gmin, lo], "color": "#f8d7da"},
                        {"range": [lo, hi], "color": "#d4edda"},
                        {"range": [hi, gmax], "color": "#f8d7da"},
                    ],
                },
            )
        )
        fig_g.update_layout(height=280, margin=dict(l=20, r=20, t=60, b=10))
        c.plotly_chart(fig_g, use_container_width=True)

    st.caption(f"Registros: {len(df):,} · Desde {df['_time'].min():%Y-%m-%d %H:%M} "
               f"hasta {df['_time'].max():%Y-%m-%d %H:%M} ({tz}) · "
               f"Última consulta: {datetime.now():%H:%M:%S}")

# ===== SERIES DE TIEMPO =====
with tab_series:
    st.subheader("Serie de tiempo (eje doble)")
    dual = st.checkbox("Usar eje Y secundario para la humedad", value=True)

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    for v in variables:
        sec = dual and v == "humidity" and "temperature" in variables
        if show_raw:
            fig.add_trace(
                go.Scatter(x=df["_time"], y=df[v], mode="lines", name=VARS_LABEL[v],
                           line=dict(color=COLORS[v], width=1), opacity=0.6),
                secondary_y=sec,
            )
        if show_rolling:
            fig.add_trace(
                go.Scatter(x=df["_time"], y=df[f"{v}_roll"], mode="lines",
                           name=f"Media móvil {v}", line=dict(color=COLORS[v], width=3, dash="dot")),
                secondary_y=sec,
            )
        if show_anomalies:
            an = df[df[f"{v}_anom"]]
            if not an.empty:
                fig.add_trace(
                    go.Scatter(x=an["_time"], y=an[v], mode="markers", name=f"Anomalías {v}",
                               marker=dict(color="black", size=9, symbol="x")),
                    secondary_y=sec,
                )
        if show_thresholds:
            lo, hi = LIMITS[v]
            fig.add_hrect(y0=lo, y1=hi, fillcolor=COLORS[v], opacity=0.07, line_width=0, secondary_y=sec)

    fig.update_layout(
        height=520,
        hovermode="x unified",
        legend=dict(orientation="h", y=1.1),
        xaxis=dict(rangeslider=dict(visible=True),
                   rangeselector=dict(buttons=[
                       dict(count=1, label="1h", step="hour", stepmode="backward"),
                       dict(count=6, label="6h", step="hour", stepmode="backward"),
                       dict(count=1, label="1d", step="day", stepmode="backward"),
                       dict(step="all", label="Todo"),
                   ])),
    )
    if "temperature" in variables:
        fig.update_yaxes(title_text="Temperatura (°C)", secondary_y=False)
    if dual and "humidity" in variables and "temperature" in variables:
        fig.update_yaxes(title_text="Humedad (%)", secondary_y=True)
    st.plotly_chart(fig, use_container_width=True)

    # Remuestreo
    st.subheader(f"⏱️ {agg_label} cada {resample_freq}")
    df_res = (
        df.set_index("_time")[variables]
        .resample(resample_freq)
        .agg(AGG_MAP[agg_label])
        .dropna(how="all")
    )
    chart_kind = st.radio("Tipo de gráfica", ["Línea", "Barras", "Área"], horizontal=True)
    long_res = df_res.reset_index().melt(id_vars="_time", var_name="Variable", value_name="Valor")
    common = dict(x="_time", y="Valor", color="Variable", color_discrete_map=COLORS)
    if chart_kind == "Línea":
        fig2 = px.line(long_res, markers=True, **common)
    elif chart_kind == "Barras":
        fig2 = px.bar(long_res, barmode="group", **common)
    else:
        fig2 = px.area(long_res, **common)
    fig2.update_layout(height=400, hovermode="x unified", xaxis_title=None)
    st.plotly_chart(fig2, use_container_width=True)

# ===== DISTRIBUCIÓN =====
with tab_dist:
    st.subheader("Distribución de valores")
    c1, c2 = st.columns([1, 3])
    var_sel = c1.selectbox("Variable", variables, format_func=lambda v: VARS_LABEL[v], key="dist_var")
    nbins = c1.slider("Número de bins", 5, 100, 30)
    marginal = c1.selectbox("Gráfica marginal", ["box", "violin", "rug", "ninguna"])
    fig_h = px.histogram(
        df, x=var_sel, nbins=nbins, marginal=None if marginal == "ninguna" else marginal,
        color_discrete_sequence=[COLORS[var_sel]],
    )
    fig_h.update_layout(height=450, bargap=0.05)
    c2.plotly_chart(fig_h, use_container_width=True)

    st.subheader("Comportamiento por hora del día")
    df_h = df.assign(hora=df["_time"].dt.hour)
    fig_b = px.box(df_h, x="hora", y=var_sel, color_discrete_sequence=[COLORS[var_sel]])
    fig_b.update_layout(height=400)
    st.plotly_chart(fig_b, use_container_width=True)

# ===== PATRONES =====
with tab_patrones:
    if "temperature" in variables and "humidity" in variables:
        st.subheader("Temperatura vs. Humedad")
        corr = df["temperature"].corr(df["humidity"])
        st.metric("Correlación de Pearson", f"{corr:.3f}" if pd.notna(corr) else "—")
        sample_n = st.slider("Máx. puntos a graficar", 500, 20000, 5000, 500)
        df_s = df.dropna(subset=["temperature", "humidity"])
        if len(df_s) > sample_n:
            df_s = df_s.sample(sample_n, random_state=1).sort_values("_time")
        fig_s = px.scatter(
            df_s, x="temperature", y="humidity", color="_time", opacity=0.7,
            color_continuous_scale="Viridis",
            labels={"temperature": "Temperatura (°C)", "humidity": "Humedad (%)", "_time": "Tiempo"},
        )
        fig_s.update_layout(height=450)
        st.plotly_chart(fig_s, use_container_width=True)
    else:
        st.info("Selecciona temperatura y humedad para ver la correlación.")

    st.subheader("🗓️ Mapa de calor: día de la semana × hora")
    hv = st.selectbox("Variable", variables, format_func=lambda v: VARS_LABEL[v], key="heat_var")
    days_es = {"Monday": "Lun", "Tuesday": "Mar", "Wednesday": "Mié", "Thursday": "Jue",
               "Friday": "Vie", "Saturday": "Sáb", "Sunday": "Dom"}
    order = list(days_es.values())
    df_hm = df.assign(hora=df["_time"].dt.hour, dia=df["_time"].dt.day_name().map(days_es))
    pivot = df_hm.pivot_table(index="dia", columns="hora", values=hv, aggfunc="mean")
    pivot = pivot.reindex([d for d in order if d in pivot.index])
    fig_hm = px.imshow(
        pivot, aspect="auto",
        color_continuous_scale="RdYlBu_r" if hv == "temperature" else "Blues",
        labels=dict(x="Hora", y="Día", color=UNITS[hv]),
    )
    fig_hm.update_layout(height=380)
    st.plotly_chart(fig_hm, use_container_width=True)

# ===== DATOS =====
with tab_datos:
    st.subheader("Estadísticas descriptivas")
    st.dataframe(df[variables].describe().T.round(2), use_container_width=True)

    st.subheader("Datos crudos")
    c1, c2 = st.columns(2)
    n_rows = c1.slider("Filas a mostrar (últimas)", 10, 1000, 50, 10)
    only_anom = c2.checkbox("Solo anomalías")

    base_cols = ["_time"] + variables
    view = df.copy()
    if only_anom:
        mask = pd.Series(False, index=view.index)
        for v in variables:
            mask |= view[f"{v}_anom"]
        view = view[mask]
    view = view[base_cols].tail(n_rows).sort_values("_time", ascending=False)
    st.dataframe(view, use_container_width=True, hide_index=True)

    st.download_button(
        "⬇️ Descargar CSV (rango completo)",
        data=df[base_cols].to_csv(index=False).encode("utf-8"),
        file_name=f"iot_telemetry_{datetime.now():%Y%m%d_%H%M%S}.csv",
        mime="text/csv",
    )

# ---------------------------
# AUTO REFRESH
# ---------------------------
if auto_refresh:
    time.sleep(refresh)
    st.rerun()
