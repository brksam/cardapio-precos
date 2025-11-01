# dashboard.py
# Dashboard Streamlit + Plotly lendo do Firestore (Admin SDK)
# - Lista produtos com filtros
# - Destaque de variações
# - Histórico de preço por produto (gráfico)
# - Botão para rodar o scraping (chama lg1.py)

import os
import sys
from datetime import datetime, timedelta, timezone
import numpy as np


import pandas as pd
import plotly.express as px
import streamlit as st

# Firebase Admin SDK
import firebase_admin
from firebase_admin import credentials, firestore

# ------------- Config -------------
PROJECT_TITLE = "Acompanhamento de Preços - Cardápio"
DEFAULT_LIMIT = 300
HISTORY_DEFAULT_DAYS = 30

# ------------- Firestore -------------
@st.cache_resource(show_spinner=False)
def init_firestore():
    import firebase_admin
    from firebase_admin import credentials, firestore
    try:
        if not firebase_admin._apps:
            if "gcp_service_account" in st.secrets:
                cred = credentials.Certificate(dict(st.secrets["gcp_service_account"]))
            else:
                cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "serviceAccountKey.json")
                cred = credentials.Certificate(cred_path)
            firebase_admin.initialize_app(cred)
        return firestore.client()
    except Exception as e:
        st.error(f"Falha ao inicializar Firestore: {e}")
        st.stop()
     

db = init_firestore()

# ------------- Helpers -------------
def ts_to_dt(x):
    try:
        return x.replace(tzinfo=None)
    except Exception:
        return x

@st.cache_data(show_spinner=False, ttl=30)
def load_products(hours: int | None, only_changed: bool, search: str | None):
    col = db.collection('products')
    docs = []

    try:
        if hours and hours > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
            q = col.where('last_seen_at', '>=', cutoff)
        else:
            q = col.limit(DEFAULT_LIMIT)

        for d in q.stream():
            row = d.to_dict()
            for key in ('last_seen_at', 'price_changed_at', 'created_at'):
                if key in row and row[key] is not None:
                    row[key] = ts_to_dt(row[key])
            docs.append(row)
    except Exception as e:
        st.error(f"Erro ao ler produtos: {e}")
        return pd.DataFrame()

    if not docs:
        return pd.DataFrame()

    df = pd.DataFrame(docs)
    # Garantir numérico
    df['current_price'] = pd.to_numeric(df.get('current_price', 0), errors='coerce')
    df['last_price'] = pd.to_numeric(df.get('last_price', np.nan), errors='coerce')
    # Fallback: se last_price ausente, usa o current_price
    df['last_price'] = df['last_price'].fillna(df['current_price'])
    # Cálculos
    df['delta'] = (df['current_price'] - df['last_price'])
    df['delta_pct'] = np.where(
        df['last_price'] > 0,
        (df['delta'] / df['last_price']) * 100,
        np.nan
    )
    # Filtro: busca por nome
    if search:
        s = search.strip().lower()
        if 'name' in df.columns:
            df = df[df['name'].str.lower().str.contains(s, na=False)]

    return df.reset_index(drop=True)

@st.cache_data(show_spinner=False, ttl=30)
def load_price_history(product_name: str, hours: int | None = None) -> pd.DataFrame:
    from unicodedata import normalize
    import re as regex

    def slugify(text: str) -> str:
        text = normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')
        text = regex.sub(r'[^a-zA-Z0-9]+', '-', text).strip('-').lower()
        return text

    pid = slugify(product_name)
    ref = db.collection('products').document(pid).collection('prices')
    q = ref.order_by('at')
    if hours and hours > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        q = q.where('at', '>=', cutoff)

    rows = []
    try:
        for s in q.stream():
            d = s.to_dict()
            at = ts_to_dt(d.get('at'))
            price = float(d.get('price', 0.0))
            rows.append({'at': at, 'price': price})
    except Exception as e:
        st.error(f"Erro ao ler histórico: {e}")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values('at').reset_index(drop=True)
    return df

def arrow(delta: float) -> str:
    if pd.isna(delta):
        return "—"
    if delta > 0:
        return "↑"
    if delta < 0:
        return "↓"
    return "—"

# ------------- UI -------------
st.set_page_config(page_title=PROJECT_TITLE, layout="wide")
st.title(PROJECT_TITLE)

def check_password():
    pwd_cfg = st.secrets.get("APP_PASSWORD", None)
    # Se não tiver senha configurada no Secrets, travamos com mensagem clara
    if not isinstance(pwd_cfg, str) or not pwd_cfg.strip():
        st.error("APP_PASSWORD não configurado nos Secrets do app. Configure e redeploy.")
        st.stop()

    if st.session_state.get("auth_ok"):
        return True

    st.title("Acesso ao painel")
    pwd = st.text_input("Senha", type="password", key="pwd_input")
    # ao apertar Enter no campo, já tenta
    if st.session_state.get("pwd_input") and st.session_state.get("last_try") != st.session_state.pwd_input:
        st.session_state["last_try"] = st.session_state.pwd_input
        if st.session_state.pwd_input == pwd_cfg:
            st.session_state["auth_ok"] = True
            st.experimental_rerun()
        else:
            st.error("Senha incorreta. Tente novamente.")

    if st.button("Entrar"):
        if st.session_state.pwd_input == pwd_cfg:
            st.session_state["auth_ok"] = True
            st.experimental_rerun()
        else:
            st.error("Senha incorreta. Tente novamente.")

    st.stop()

# chame logo após set_page_config e antes de qualquer UI
check_password()


# Sidebar - Filtros
st.sidebar.header("Filtros")
hours_map = {
    "Últimas 24h": 24,
    "Últimos 7 dias": 24*7,
    "Últimos 30 dias": 24*30,
    "Tudo (limitado)": None
}
hours_label = st.sidebar.selectbox("Período", list(hours_map.keys()), index=0)
hours = hours_map[hours_label]
only_changed = st.sidebar.checkbox("Somente itens que mudaram", value=False)
search_term = st.sidebar.text_input("Buscar por nome (contém):", value="")

# Ações
colA, colB = st.sidebar.columns(2)
with colA:
    refresh = st.button("Atualizar")
with colB:
    run_scrape = st.button("Rodar scraping")

# Rodar scraping (executa lg1.py)
if run_scrape:
    with st.spinner("Executando scraping..."):
        try:
            import subprocess
            cmd = [sys.executable, "lg1.py"]  # usa o Python do venv atual
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            st.success("Scraping concluído.")
            with st.expander("Saída do scraping"):
                st.code(result.stdout or "(sem stdout)")
                if result.stderr:
                    st.error(result.stderr)
        except subprocess.TimeoutExpired:
            st.error("Scraping demorou demais (timeout).")
        except Exception as e:
            st.error(f"Erro ao rodar scraping: {e}")

# Carrega dados
with st.spinner("Carregando produtos..."):
    df = load_products(hours, only_changed, search_term)

if df.empty:
    st.warning("Nenhum produto encontrado com os filtros aplicados.")
    st.stop()

# KPI cards
kpi1, kpi2, kpi3, kpi4 = st.columns(4)
total = len(df)
mudaram = df['delta'].fillna(0).ne(0).sum()
maior_alta = df.sort_values('delta', ascending=False).head(1)
maior_queda = df.sort_values('delta', ascending=True).head(1)

kpi1.metric("Produtos listados", f"{total}")
kpi2.metric("Mudanças no período", f"{mudaram}")
if not maior_alta.empty:
    kpi3.metric("Maior alta (R$)", f"{maior_alta.iloc[0]['delta']:.2f}", delta=f"{maior_alta.iloc[0]['name']}")
else:
    kpi3.metric("Maior alta (R$)", "—")
if not maior_queda.empty:
    kpi4.metric("Maior queda (R$)", f"{maior_queda.iloc[0]['delta']:.2f}", delta=f"{maior_queda.iloc[0]['name']}")
else:
    kpi4.metric("Maior queda (R$)", "—")

# Tabela resumida
show_cols = ['name', 'current_price', 'last_price', 'delta', 'delta_pct', 'last_seen_at', 'price_changed_at']
for c in show_cols:
    if c not in df.columns:
        df[c] = pd.NA

df_view = df[show_cols].copy()
df_view['delta_fmt'] = df_view['delta'].apply(lambda d: f"{arrow(d)} {d:.2f}" if pd.notna(d) else "—")
df_view['delta_pct_fmt'] = df_view['delta_pct'].apply(lambda p: f"{p:+.2f}%" if pd.notna(p) else "—")

st.subheader("Produtos")
st.caption("Delta = Atual - Último. Seta indica direção (+↑, -↓).")
st.dataframe(
    df_view.rename(columns={
        'name': 'Nome',
        'current_price': 'Preço Atual (R$)',
        'last_price': 'Último Preço (R$)',
        'delta_fmt': 'Delta',
        'delta_pct_fmt': 'Delta %',
        'last_seen_at': 'Visto em',
        'price_changed_at': 'Mudou em'
    })[['Nome','Preço Atual (R$)','Último Preço (R$)','Delta','Delta %','Visto em','Mudou em']],
    use_container_width=True,
    hide_index=True
)

# Seletor de produto para histórico
st.subheader("Histórico de preço por produto")
sel_name = st.selectbox(
    "Escolha o produto",
    options=df['name'].tolist(),
    index=0 if not df.empty else None
)

hist_hours_map = {
    "Últimos 7 dias": 24*7,
    "Últimos 30 dias": 24*30,
    "Tudo": None
}
hist_label = st.radio("Período do histórico", list(hist_hours_map.keys()), horizontal=True, index=1)
hist_hours = hist_hours_map[hist_label]

with st.spinner("Carregando histórico..."):
    hdf = load_price_history(sel_name, hist_hours)

if hdf.empty:
    st.info("Sem histórico para este produto no período selecionado.")
else:
    fig = px.line(
        hdf, x='at', y='price', markers=True,
        title=f"Histórico - {sel_name}",
        labels={'at': 'Data/Hora', 'price': 'Preço (R$)'}
    )
    fig.update_layout(height=420, margin=dict(l=20, r=20, t=50, b=20))
    st.plotly_chart(fig, use_container_width=True)

st.caption("Atualize o scraping pelo botão na barra lateral para refletir os preços mais recentes.")
