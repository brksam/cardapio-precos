# buscaEcriaGrafico.py
import os
import re
import unicodedata

import firebase_admin
from firebase_admin import credentials, firestore
import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

from datetime import datetime, timedelta, timezone

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

# ---------- Busca ----------
def search_products_prefix(db, term: str, limit: int = 10) -> list[dict]:
    """
    Busca por prefixo em 'name' (case-sensitive).
    Dica: Se você tiver um campo 'name_lower', use order_by('name_lower') com term.lower().
    """
    term = term.strip()
    q = (db.collection('products')
          .order_by('name')
          .start_at([term])
          .end_at([term + '\uf8ff'])
          .limit(limit))
    return [doc.to_dict() for doc in q.stream()]

# ---------- Histórico ----------
def get_price_history_df(db, product_name: str, hours: int | None = None) -> pd.DataFrame:
    ref = product_ref(db, product_name)
    q = ref.collection('prices').order_by('at')
    if hours:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        q = q.where('at', '>=', cutoff)
    rows = []
    for s in q.stream():
        d = s.to_dict()
        at = d['at']
        if hasattr(at, 'replace'):
            at = at.replace(tzinfo=None)
        rows.append({'at': at, 'price': float(d['price'])})
    df = pd.DataFrame(rows).sort_values('at').reset_index(drop=True) if rows else pd.DataFrame()
    return df

# ---------- Gráfico ----------
def plot_history(df: pd.DataFrame, product_name: str, save_path: str | None = None, style='line'):
    if df.empty:
        print("Sem dados para plotar.")
        return
    sns.set_style("whitegrid")
    plt.figure(figsize=(8,4))

    if style == 'bar':
        plt.bar(df['at'], df['price'], color='royalblue')
    else:
        plt.plot(df['at'], df['price'], marker='o', color='royalblue', linewidth=2)

    plt.title(f'Histórico de Preços - {product_name}')
    plt.xlabel('Data/Hora')
    plt.ylabel('Preço (R$)')
    plt.xticks(rotation=45, ha='right')
    plt.tight_layout()
    if save_path:
        plt.savefig(save_path)
        print(f"Gráfico salvo em: {save_path}")
    plt.show()

# ---------- Execução (CLI simples) ----------
if __name__ == "__main__":
    db = init_firestore()
    termo = input("Digite um nome (ou prefixo) do produto: ").strip()
    sugestoes = search_products_prefix(db, termo, limit=10)
    if not sugestoes:
        print("Nenhum produto encontrado para o termo.")
        exit(0)
    print("Sugestões:")
    for i, s in enumerate(sugestoes, 1):
        print(f"{i}. {s.get('name')} (atual: {s.get('current_price')}, last: {s.get('last_price')})")

    idx = int(input("Escolha o número do produto: "))
    escolhido = sugestoes[idx - 1]['name']

    df = get_price_history_df(db, escolhido, hours=24*30)
    plot_history(df, escolhido, save_path=f"grafico_{slugify(escolhido)}.png", style='line')