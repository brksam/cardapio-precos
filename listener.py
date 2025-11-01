# listener.py
import os
import firebase_admin
from firebase_admin import credentials, firestore

def init_firestore():
    cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "serviceAccountKey.json")
    if not os.path.isfile(cred_path):
        raise FileNotFoundError(f"Credencial não encontrada: {cred_path}")
    if not firebase_admin._apps:
        cred = credentials.Certificate(cred_path)
        firebase_admin