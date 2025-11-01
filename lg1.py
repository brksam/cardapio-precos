# lg1.py
# Scraping com Playwright + Firestore (upsert em lote + histórico de preço)
# Filtragem de itens indesejados e modal de promoções fechada automaticamente.

import os
import re as regex
import unicodedata
from datetime import datetime, timezone
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# Firestore Admin SDK
import firebase_admin
from firebase_admin import credentials, firestore
from google.api_core.retry import Retry
try:
    from google.api_core.exceptions import DeadlineExceeded, GoogleAPIError
except Exception:
    class DeadlineExceeded(Exception): ...
    class GoogleAPIError(Exception): ...
from google.cloud.firestore_v1 import Increment

# Playwright
from playwright.sync_api import sync_playwright

# ---------------- Configurações ----------------
URL = "https://app.cardapioweb.com/acai_moto_food"

# Seletores do produto (CSS)
NAME_SEL = 'h3.text-base.font-medium.leading-6.text-gray-700.line-clamp-2'
DESC_SEL = '.text-sm.font-light.text-gray-500.line-clamp-3'
PRICE_CURRENT_SEL = 'span.text-base.text-green-500'
PRICE_PREV_SEL = 'span.text-sm.text-gray-500.line-through'
# Em CSS, ':' precisa ser escapado:
PRICE_BASE_SEL = 'div.mt-3.text-base.text-gray-700.md\\:mt-6'

# Variáveis de ambiente
HEADLESS = os.getenv("HEADLESS", "1") != "0"
MAX_ITEMS = int(os.getenv("MAX_ITEMS", "0"))  # 0 = sem limite
DEBUG_LOG = os.getenv("DEBUG_LOG", "0") == "1"

# ---------------- Firestore ----------------
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
    text = regex.sub(r'[^a-zA-Z0-9]+', '-', text).strip('-').lower()
    return text
# ---------------- Utils ----------------
def parse_price(text: str) -> float:
    if not text:
        return 0.0
    m = regex.search(r'(\d+(?:[.,]\d{2})?)', text)
    return float(m.group(1).replace(',', '.')) if m else 0.0

def auto_scroll(page):
    page.evaluate("""
        () => new Promise(resolve => {
            let total = 0;
            const distance = 800;
            const timer = setInterval(() => {
                window.scrollBy(0, distance);
                total += distance;
                if (total >= document.body.scrollHeight - window.innerHeight) {
                    clearInterval(timer);
                    setTimeout(resolve, 500);
                }
            }, 200);
        })
    """)

def first_text(loc):
    try:
        return loc.inner_text().strip() if loc.count() > 0 else ""
    except Exception:
        return ""

def close_promotions_if_any(page, attempts: int = 3):
    # Evita operar se a página já estiver fechada
    try:
        if hasattr(page, "is_closed") and page.is_closed():
            return
    except Exception:
        return

    selectors = [
        '.z-30.flex.items-center.justify-between.p-4 > .MuiButtonBase-root',  # seu "X"
        'button.MuiButtonBase-root[aria-label="Close"]',
        'button[aria-label="Fechar"]',
        'button[aria-label="close"]',
        'button:has-text("×")',
        'button:has-text("X")',
    ]

    for _ in range(attempts):
        closed_this_round = False

        # 1) Seletores diretos
        for sel in selectors:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click(timeout=1500)
                    closed_this_round = True
                    break
            except Exception:
                pass

        # 2) Role/name (fechar/close/x/×)
        if not closed_this_round:
            try:
                btn = page.get_by_role("button", name=regex.compile(r"(fechar|close|x|×)", regex.I))
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click(timeout=1500)
                    closed_this_round = True
            except Exception:
                pass

        # 3) Escape como fallback
        if not closed_this_round:
            try:
                page.keyboard.press('Escape')
            except Exception:
                pass

        try:
            page.wait_for_timeout(300)
        except Exception:
            return

        try:
            if page.locator('.z-30.flex.items-center.justify-between.p-4').count() == 0:
                break
        except Exception:
            break

def is_unwanted_product(name: str, price: float) -> bool:
    n = (name or "").strip()
    if n.lower().startswith("tel novo"):
        return True
    if regex.search(r"\(\d{2}\)\s*\d{4,5}-\d{4}", n):
        return True
    if not price or price <= 0:
        return True
    return False

# ---------------- Scraping ----------------
def scrape_products(max_items=None, headless=True, debug=False) -> list[dict]:
    products = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(30000)
        try:
            page.goto(URL, wait_until='networkidle')

            # Fecha modal ao entrar
            close_promotions_if_any(page)

            # Garante nomes e fecha modal de novo
            page.wait_for_selector(NAME_SEL, timeout=20000)
            close_promotions_if_any(page)

            # Scroll e fecha modal de novo, se aparecer
            auto_scroll(page)
            close_promotions_if_any(page)

            name_locator = page.locator(NAME_SEL)
            count = name_locator.count()
            if count == 0 and debug:
                page.screenshot(path="debug_sem_itens.png", full_page=True)
                print("DEBUG: Nenhum item encontrado. Screenshot salvo em debug_sem_itens.png")

            take = count if not max_items or max_items <= 0 else min(count, max_items)

            seen = set()  # deduplicação por slug do nome

            for i in range(take):
                name_el = name_locator.nth(i)
                name = name_el.inner_text().strip()
                if not name:
                    continue

                pid = slugify(name)
                if pid in seen:
                    continue
                seen.add(pid)

                price_current_text, price_prev_text, price_base_text, desc_text = "", "", "", ""
                found = False

                # Sobe até 6 ancestrais (div/article/li)
                for depth in range(1, 7):
                    container = name_el.locator(f'xpath=ancestor::*[self::div or self::article or self::li][{depth}]')
                    if container.count() == 0:
                        continue

                    price_current_text = first_text(container.locator(PRICE_CURRENT_SEL).first)
                    price_prev_text    = first_text(container.locator(PRICE_PREV_SEL).first)
                    price_base_text    = first_text(container.locator(PRICE_BASE_SEL).first)
                    desc_text          = first_text(container.locator(DESC_SEL).first)

                    # Fallback de base dentro do container (sem exigir md:mt-6)
                    if not price_base_text:
                        price_base_text = first_text(
                            container.locator(
                                'xpath=.//div[contains(@class,"mt-3") and contains(@class,"text-base") and contains(@class,"text-gray-700")]'
                            ).first
                        )

                    if price_current_text or price_base_text:
                        found = True
                        break

                # Fallback: busca logo após o h3
                if not found:
                    price_current_text = first_text(
                        name_el.locator('xpath=following::span[contains(@class,"text-green-500")][1]')
                    )

                    # 1) exigir md:mt-6
                    price_base_text = first_text(
                        name_el.locator(
                            'xpath=following::div[contains(@class,"mt-3") and contains(@class,"text-base") and '
                            'contains(@class,"text-gray-700") and contains(@class,"md:mt-6")][1]'
                        )
                    )
                    # 2) sem md:mt-6
                    if not price_base_text:
                        price_base_text = first_text(
                            name_el.locator(
                                'xpath=following::div[contains(@class,"mt-3") and contains(@class,"text-base") and '
                                'contains(@class,"text-gray-700")][1]'
                            )
                        )

                    price_prev_text = first_text(
                        name_el.locator('xpath=following::span[contains(@class,"line-through")][1]')
                    )
                    desc_text = first_text(
                        name_el.locator('xpath=following::*[contains(@class,"text-sm") and contains(@class,"text-gray-500")][1]')
                    )

                price_current = parse_price(price_current_text)
                price_base = parse_price(price_base_text)
                chosen_price = price_current if price_current > 0 else price_base

                if DEBUG_LOG:
                    print(f"[DEBUG] {len(seen)}/{take} '{name}' | cur='{price_current_text}' base='{price_base_text}' prev='{price_prev_text}' -> chosen={chosen_price}")

                # Pular indesejados / sem preço
                if is_unwanted_product(name, chosen_price):
                    if DEBUG_LOG:
                        print(f"[SKIP] '{name}' pulado (indesejado/sem preço).")
                    continue

                products.append({
                    'name': name,
                    'price': chosen_price,
                    'description': (desc_text or "")[:120],
                    'extracted_prev_price': parse_price(price_prev_text),
                    'extracted_base_price': price_base,
                    'extracted_current_price': price_current,
                })

        except Exception as e:
            if debug:
                try:
                    page.screenshot(path="debug_erro.png", full_page=True)
                    print("DEBUG: Erro no scraping. Screenshot salvo em debug_erro.png")
                except Exception:
                    pass
            raise e
        finally:
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass

    return products
# ---------------- Upsert em lote ----------------
def batch_upsert_products(db, products: list[dict], batch_size: int = 400) -> list[dict]:
    if not products:
        return []  # garante retorno de lista
    now = datetime.now(timezone.utc)

    # Filtra (garantia extra): remove indesejados e sem preço
    filtered = []
    for p in products:
        price = float(p.get('price', 0.0))
        name = p.get('name', '')
        if is_unwanted_product(name, price):
            continue
        filtered.append(p)
    products = filtered

    # 1) Refs e mapa
    refs, by_id = [], {}
    for p in products:
        pid = slugify(p['name'])
        ref = db.collection('products').document(pid)
        refs.append(ref)
        by_id[pid] = {'ref': ref, 'product': p}

    # 2) Lê todos de uma vez
    existing = {}
    try:
        snapshots = db.get_all(refs, field_paths=('current_price', 'name', 'last_price'), timeout=20, retry=Retry())
        for snap in snapshots:
            if snap.exists:
                existing[snap.id] = snap.to_dict()
    except DeadlineExceeded:
        print("AVISO: Timeout ao ler documentos existentes. Tratando como novos.")
        existing = {}
    except GoogleAPIError as e:
        print(f"AVISO: Falha ao ler documentos existentes: {e}. Prosseguindo.")
        existing = {}

    results = []
    writes = []

    # 3) Monta operações
    for pid, item in by_id.items():
        ref = item['ref']
        p = item['product']
        current_price = float(p.get('price', 0.0))
        description = p.get('description', '')
        name = p.get('name', '').strip()

        extracted_prev = float(p.get('extracted_prev_price', 0.0))
        extracted_base = float(p.get('extracted_base_price', 0.0))
        extracted_current = float(p.get('extracted_current_price', 0.0))

        if pid in existing:
            prev = existing[pid]
            prev_price = float(prev.get('current_price', 0.0))
            changed = (current_price != prev_price)

            update_data = {
                'name': name,
                'description': description,
                'last_seen_at': now,
                'display_prev_price': extracted_prev,
                'display_base_price': extracted_base,
                'display_current_green': extracted_current,
            }
            if changed:
                update_data.update({
                    'last_price': prev_price,
                    'current_price': current_price,
                    'price_changed_at': now,
                    'change_count': Increment(1),
                })
                subdoc = ref.collection('prices').document() # gera ID automático writes.append(('set', subdoc, {'price': current_price, 'at': now}, False))

            writes.append(('set', ref, update_data, True))
            results.append({
                'name': name,
                'prev_price': prev_price,
                'current_price': current_price,
                'changed': changed,
                'delta': round(current_price - prev_price, 2) if changed else 0.0
            })
        else:
            create_data = {
                'name': name,
                'description': description,
                'current_price': current_price,
                'last_price': current_price,
                'created_at': now,
                'last_seen_at': now,
                'change_count': 0,
                'display_prev_price': extracted_prev,
                'display_base_price': extracted_base,
                'display_current_green': extracted_current,
            }
            writes.append(('set', ref, create_data, False))
            ref.collection('prices').document() # gera ID automático writes.append(('set', subdoc, {'price': current_price, 'at': now}, False))
            results.append({
                'name': name,
                'prev_price': None,
                'current_price': current_price,
                'changed': False,
                'delta': 0.0
            })

        # 4) Commit em lotes
    def commit_batch(pending_ops): 
        batch = db.batch() 
        ops_in_batch = 0 
        for op in pending_ops: 
            if op[0] == 'set': _, ref, data, merge = op 
            batch.set(ref, data, merge=merge) 
            ops_in_batch += 1 # Segurança extra: se por alguma razão estourar o tamanho do batch if ops_in_batch >= 450: # abaixo do limite de 500 batch.commit() batch = db.batch() ops_in_batch = 0 if ops_in_batch: batch.commit()
    
    chunk = []
    for op in writes:
        chunk.append(op)
        if len(chunk) >= batch_size:
            commit_batch(chunk)
            chunk = []
    if chunk:
        commit_batch(chunk)
    return results  # <- não pode faltar

    

# ---------------- Utilitário de limpeza (opcional) ----------------
def delete_product_and_history(db, product_name: str):
    """Apaga um produto específico e toda a subcoleção 'prices'."""
    pid = slugify(product_name)
    ref = db.collection('products').document(pid)

    # Apaga subcoleção 'prices'
    prices = ref.collection('prices').stream()
    batch = db.batch()
    count = 0
    for s in prices:
        batch.delete(s.reference)
        count += 1
        if count % 400 == 0:
            batch.commit()
            batch = db.batch()
    if count % 400 != 0:
        batch.commit()

    # Apaga o doc principal
    ref.delete()
    print(f"Apagado: {product_name} (slug={pid}), {count} históricos removidos.")


# -------- Main --------
def main():
    print(f"Iniciando scraping. HEADLESS={HEADLESS} | MAX_ITEMS={MAX_ITEMS or 'sem limite'}")
    db = init_firestore()

    # Scraping
    products = scrape_products(
        max_items=(MAX_ITEMS if MAX_ITEMS > 0 else None),
        headless=HEADLESS,
        debug=True
    )

    if not products:
        print("Nenhum produto encontrado. Verifique seletores e use HEADLESS=0 para depurar visualmente.")
        return

    # Upsert em lote
    results = batch_upsert_products(db, products, batch_size=200) or []

    # Tratar caso vazio (nenhuma alteração)
    if not results:
        print("\nResumo de alterações:")
        print("- Nenhuma alteração encontrada.")
        return

    # Resumo
    novos, mudaram, iguais = 0, 0, 0
    print("\nResumo de alterações:")
    for r in results:
        if r['prev_price'] is None:
            novos += 1
            print(f"- NOVO: {r['name']} | atual R$ {r['current_price']:.2f}")
        elif r['changed']:
            mudaram += 1
            print(f"- MUDOU: {r['name']} | atual R$ {r['current_price']:.2f} (Δ {r['delta']:+.2f})")
        else:
            iguais += 1
            print(f"- IGUAL: {r['name']} | atual R$ {r['current_price']:.2f}")

    print(f"\nTotais -> Novos: {novos} | Mudaram: {mudaram} | Iguais: {iguais}")


if __name__ == "__main__":
    main()