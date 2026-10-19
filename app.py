import streamlit as st
from influxdb_client import InfluxDBClient
import pandas as pd
import plotly.express as px

# ---------------------------
# CONFIG
# ---------------------------
url = "https://us-east-1-1.aws.cloud2.influxdata.com"
token = "5FESPQw5t4qA4GgZmyeZ9mu7u1sbtauo4Hh8tjRi1E8sdrIgzXAX5ESk4n5MmcpAjJWEGgEKklFX0LNasnUmng=="
org = "miguelcmo"
bucket = "iot_telemetry_data"

# ---------------------------
# STREAMLIT UI
# ---------------------------
st.set_page_config(page_title="IoT Dashboard", layout="wide")

st.title("🌡️ IoT Telemetry Dashboard")
st.markdown("Monitoreo de temperatura y humedad en tiempo real")

# Sidebar
st.sidebar.header("Configuración")

time_range = st.sidebar.selectbox(
    "Rango de tiempo",
    ["-1h", "-6h", "-12h", "-24h", "-7d"],
    index=3
)

refresh = st.sidebar.slider("Auto-refresh (segundos)", 0, 60, 10)

# ---------------------------
# DATA FUNCTION
# ---------------------------
@st.cache_data(ttl=10)
def load_data(time_range):
    client = InfluxDBClient(url=url, token=token, org=org)
    query_api = client.query_api()

    query = f'''
    from(bucket: "{bucket}")
      |> range(start: {time_range})
      |> filter(fn: (r) => r["_field"] == "temperature" or r["_field"] == "humidity")
      |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
      |> keep(columns: ["_time", "temperature", "humidity"])
    '''

    result = query_api.query_data_frame(query)

    if isinstance(result, list):
        df = pd.concat(result)
    else:
        df = result

    if df.empty:
        return df

    df["_time"] = pd.to_datetime(df["_time"])
    df = df.sort_values("_time")

    return df

# ---------------------------
# LOAD DATA
# ---------------------------
df = load_data(time_range)

# ---------------------------
# METRICS
# ---------------------------
if not df.empty:
    col1, col2, col3, col4 = st.columns(4)

    col1.metric("🌡️ Temp actual", f"{df['temperature'].iloc[-1]:.2f} °C")
    col2.metric("💧 Humedad actual", f"{df['humidity'].iloc[-1]:.2f} %")
    col3.metric("📊 Temp promedio", f"{df['temperature'].mean():.2f} °C")
    col4.metric("📊 Humedad promedio", f"{df['humidity'].mean():.2f} %")

# ---------------------------
# CHARTS
# ---------------------------
if not df.empty:

    st.subheader("📈 Serie de tiempo")

    fig = px.line(
        df,
        x="_time",
        y=["temperature", "humidity"],
        title="Temperature & Humidity"
    )

    st.plotly_chart(fig, use_container_width=True)

    # ---------------------------
    # RESAMPLING
    # ---------------------------
    st.subheader("⏱️ Promedio cada 10 minutos")

    #df_resampled = df.set_index("_time").resample("10min").mean().dropna()
    # Asegurar tipos numéricos
    df["temperature"] = pd.to_numeric(df["temperature"], errors="coerce")
    df["humidity"] = pd.to_numeric(df["humidity"], errors="coerce")
    
    # Asegurar datetime index
    df["_time"] = pd.to_datetime(df["_time"])
    
    # Resample SOLO columnas numéricas
    df_resampled = (
        df.set_index("_time")[["temperature", "humidity"]]
        .resample("10min")
        .mean()
        .dropna()
    )

    fig2 = px.line(
        df_resampled,
        x=df_resampled.index,
        y=["temperature", "humidity"],
        title="Smoothed Data (10min avg)"
    )

    st.plotly_chart(fig2, use_container_width=True)

    # ---------------------------
    # RAW DATA
    # ---------------------------
    st.subheader("📋 Datos crudos")
    st.dataframe(df.tail(50))

else:
    st.warning("No hay datos disponibles en este rango de tiempo")

# ---------------------------
# AUTO REFRESH
# ---------------------------
if refresh > 0:
    st.rerun()
