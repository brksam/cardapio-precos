# analiseTempo.py
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone

import firebase_admin
from firebase_admin import credentials, firestore
import pandas as pd

# ---------- Firebase ----------
def init_firestore():
    cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "serviceAccountKey.json")
    if not os.path.isfile(cred_path):
        raise FileNotFoundError(f"Credencial não encontrada: {cred_path}")
    if not firebase_admin._apps:
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
    return firestore.client()

def slugify(text: str) -> str:
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'[^a-zA-Z0-9]+', '-', text).strip('-').lower()

def product_ref(db, name: str):
    return db.collection('products').document(slugify(name))

# ---------- Histórico e análises ----------
def get_price_history(db, product_name: str, hours: int | None = None) -> pd.DataFrame:
    """
    Retorna DataFrame com colunas: at (datetime), price (float).
    Se hours for informado, filtra por janela de tempo.
    """
    ref = product_ref(db, product_name)
    q = ref.collection('prices').order_by('at')
    if hours:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        q = q.where('at', '>=', cutoff)

    rows = []
    for s in q.stream():
        d = s.to_dict()
        at = d['at']
        # Converte Firestore Timestamp -> datetime naive (para pandas)
        if hasattr(at, 'replace'):
            at = at.replace(tzinfo=None)
        rows.append({'at': at, 'price': float(d['price'])})
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values('at').reset_index(drop=True)
    return df

def compute_metrics(df: pd.DataFrame, window: int = 7) -> dict:
    """
    Calcula métricas:
    - last_price, first_price, delta_abs, delta_pct
    - moving_avg (último valor), slope (tendência linear simples)
    """
    if df.empty:
        return {}
    s = df['price']
    last_price = float(s.iloc[-1])
    first_price = float(s.iloc[0])
    delta_abs = round(last_price - first_price, 2)
    delta_pct = round((delta_abs / first_price) * 100, 2) if first_price else 0.0

    # média móvel
    ma = s.rolling(window=window, min_periods=1).mean().iloc[-1]

    # tendência (slope) via regressão linear simples (índice do tempo -> preço)
    try:
        import numpy as np
        x = np.arange(len(s))
        slope = float(np.polyfit(x, s.values, 1)[0])
    except Exception:
        slope = None

    return {
        'first_price': first_price,
        'last_price': last_price,
        'delta_abs': delta_abs,
        'delta_pct': delta_pct,
        'moving_avg': round(float(ma), 2),
        'slope': slope
    }

def get_recent_changes(db, hours: int = 24, limit: int = 100) -> list[dict]:
    """
    Retorna produtos com price_changed_at nas últimas 'hours' horas.
    Obs.: só retorna docs que tiveram mudança (price_changed_at setado).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    q = (db.collection('products')
          .where('price_changed_at', '>=', cutoff)
          .order_by('price_changed_at')
          .limit(limit))
    return [doc.to_dict() for doc in q.stream()]

def get_top_movers(db, hours: int = 24, top: int = 5, by='abs') -> list[dict]:
    """
    Retorna top produtos por variação no período:
    - by='abs': maior variação absoluta
    - by='up': maiores altas
    - by='down': maiores quedas
    """
    items = get_recent_changes(db, hours=hours, limit=1000)
    # Calcula delta (current_price - last_price)
    for it in items:
        cp = float(it.get('current_price', 0.0))
        lp = float(it.get('last_price', cp))
        it['delta'] = round(cp - lp, 2)

    if by == 'up':
        items = sorted(items, key=lambda x: x['delta'], reverse=True)
    elif by == 'down':
        items = sorted(items, key=lambda x: x['delta'])
    else:
        items = sorted(items, key=lambda x: abs(x['delta']), reverse=True)

    return items[:top]

# ---------- Execução de exemplo ----------
if __name__ == "__main__":
    db = init_firestore()
    # Exemplo: histórico + métricas de um produto
    produto = "Açaí 300ml"  # ajuste conforme seu Firestore
    df = get_price_history(db, produto, hours=720)  # último mês
    print("Registros no histórico:", len(df))
    print("Métricas:", compute_metrics(df))

    # Exemplo: mudanças recentes e top movers
    recent = get_recent_changes(db, hours=24)
    print("Mudanças nas últimas 24h:", len(recent))
    top = get_top_movers(db, hours=24, top=5, by='abs')
    for i in top:
        print(f"- {i['name']}: Δ {i['delta']} (atual {i.get('current_price')}, last {i.get('last_price')})")