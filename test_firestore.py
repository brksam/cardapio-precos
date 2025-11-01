# test_firestore.py
import os
from datetime import datetime, timezone
import firebase_admin
from firebase_admin import credentials, firestore

def init_firestore():
    cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "serviceAccountKey.json")
    if not os.path.isfile(cred_path):
        raise FileNotFoundError(f"Credencial não encontrada: {cred_path}")
    if not firebase_admin._apps:
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
    # Se você criou um DB com nome customizado, defina database="(default)" ou o nome:
    return firestore.client()  # ou firestore.client(database="(default)")

if __name__ == "__main__":
    try:
        db = init_firestore()
        ping = db.collection("health").document("ping")
        ping.set({"ts": datetime.now(timezone.utc)})
        print("Firestore OK:", ping.get().to_dict())
    except Exception as e:
        print("Erro ao conectar ao Firestore:", e)