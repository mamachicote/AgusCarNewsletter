"""
diagnostico.py
==============

Herramienta de diagnóstico para verificar que los scrapers ven resultados
y que los selectores CSS siguen funcionando.

NO filtra por precio / km / año / zona: muestra TODO lo que llega desde
Mercado Libre y Kavak, con conteos y ejemplos crudos. Tampoco manda mail.

Uso:
  python diagnostico.py              # corre todo el diagnóstico
  python diagnostico.py --model fit  # solo una query de ML

Pensado para:
  1. Correr localmente si sospechás que los selectores cambiaron.
  2. Correr en GitHub Actions (workflow_dispatch) para ver qué HTML
     llega desde los runners de la nube, que a veces difiere del
     que ves desde tu IP de casa (ML puede bloquear datacenter IPs).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Dict, List, Optional
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

from buscador_autos import (
    BENCHMARKS,
    HEADERS,
    HTTP_TIMEOUT,
    KAVAK_API,
    KAVAK_HTML,
    MAX_PRICE_USD,
    _parse_ml_card,
    _build_ml_url,
    detect_model_key,
    extract_km,
    extract_year,
    parse_int,
)


SEP = "=" * 72


def banner(text: str) -> None:
    print()
    print(SEP)
    print(f"  {text}")
    print(SEP)


# ---------------------------------------------------------------------------
# Mercado Libre
# ---------------------------------------------------------------------------

def diagnose_ml_one(query: str, sample_size: int = 5) -> None:
    url = _build_ml_url(query)
    print(f"\n--- ML · query='{query}' ---")
    print(f"URL: {url}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=HTTP_TIMEOUT)
    except Exception as exc:
        print(f"  ✗ error de red: {exc!r}")
        return

    print(f"  HTTP {resp.status_code} · body={len(resp.text)} bytes · final URL={resp.url}")
    if resp.status_code != 200:
        print(f"  ✗ ML respondió con {resp.status_code}, probablemente bloqueado")
        preview = resp.text[:400].replace("\n", " ")
        print(f"  preview: {preview!r}")
        return

    soup = BeautifulSoup(resp.text, "lxml")

    # Contamos cuántas matches tiene cada selector candidato.
    candidates = [
        "li.ui-search-layout__item",
        "div.ui-search-result__wrapper",
        "div.andes-card.poly-card",
        "div.poly-card",
        "div[class*='ui-search']",
    ]
    for sel in candidates:
        print(f"  sel={sel!r}: {len(soup.select(sel))}")

    cards = soup.select(
        "li.ui-search-layout__item, div.ui-search-result__wrapper, div.andes-card.poly-card"
    )
    print(f"  total cards candidatas: {len(cards)}")

    if not cards:
        # Dumpear un fragmento para entender qué tipo de página llegó.
        print("  ⚠ cero cards — es posible que ML haya servido captcha/redirect.")
        title_tag = soup.select_one("title")
        print(f"  <title>: {title_tag.get_text(strip=True) if title_tag else '—'}")
        return

    parsed_usd = 0
    parsed_any = 0
    rejected_no_usd = 0
    rejected_no_title = 0
    samples: List[Dict] = []

    for card in cards:
        parsed_any += 1
        # Intento con el parser real (que descarta si no es USD)
        try:
            car = _parse_ml_card(card)
        except Exception as exc:
            print(f"  ✗ parse error en card: {exc!r}")
            continue
        if car:
            parsed_usd += 1
            if len(samples) < sample_size:
                samples.append({
                    "title": car.title,
                    "price_usd": car.price_usd,
                    "km": car.km,
                    "year": car.year,
                    "location": car.location,
                    "url": car.url[:80] + ("…" if len(car.url) > 80 else ""),
                    "model_key": detect_model_key(car.title),
                })
            continue

        # Diagnóstico del rechazo: ¿tenía título? ¿precio en pesos?
        a = card.select_one("a.poly-component__title") or card.select_one("h2 a") or card.select_one("a")
        if not a:
            rejected_no_title += 1
            continue
        title = a.get_text(strip=True)
        block = card.get_text(" ", strip=True)
        has_usd = "US$" in block
        has_ars = "$" in block and not has_usd
        if not has_usd and has_ars:
            rejected_no_usd += 1

    print(f"  resumen: cards_vistas={parsed_any} · parseadas_USD={parsed_usd} · "
          f"rechazadas_sin_USD(ARS)={rejected_no_usd} · rechazadas_sin_título={rejected_no_title}")

    if samples:
        print(f"  primeras {len(samples)} en USD:")
        for s in samples:
            print(f"    · {s['title'][:80]}")
            print(f"        USD {s['price_usd']} · {s['km']} km · {s['year']} · "
                  f"{s['location']} · model_key={s['model_key']}")
    else:
        print("  ⚠ 0 listings en USD — probablemente la página llegó en ARS o los selectores de precio cambiaron.")
        # Dump de una card cruda para inspección manual
        first = cards[0]
        txt = first.get_text(" | ", strip=True)[:400]
        print(f"  sample card text: {txt!r}")


def diagnose_ml_all() -> None:
    banner("MERCADO LIBRE — diagnóstico por modelo")
    queries = [f"{m} automatico" for m in BENCHMARKS]
    for q in queries:
        diagnose_ml_one(q, sample_size=3)


# ---------------------------------------------------------------------------
# Kavak
# ---------------------------------------------------------------------------

def diagnose_kavak() -> None:
    banner("KAVAK — diagnóstico")

    print(f"\n--- Kavak API ---")
    print(f"URL: {KAVAK_API}")
    try:
        resp = requests.get(KAVAK_API, headers=HEADERS, timeout=HTTP_TIMEOUT)
        print(f"  HTTP {resp.status_code} · body={len(resp.text)} bytes")
        if resp.status_code == 200:
            try:
                data = resp.json()
                print(f"  JSON OK · top-level keys: {list(data.keys()) if isinstance(data, dict) else type(data).__name__}")
                preview = json.dumps(data, ensure_ascii=False)[:300]
                print(f"  preview: {preview}")
            except Exception as exc:
                print(f"  ✗ no parsea como JSON: {exc!r}")
        else:
            print(f"  preview: {resp.text[:300]!r}")
    except Exception as exc:
        print(f"  ✗ error de red: {exc!r}")

    print(f"\n--- Kavak HTML ---")
    print(f"URL: {KAVAK_HTML}")
    try:
        resp = requests.get(KAVAK_HTML, headers=HEADERS, timeout=HTTP_TIMEOUT)
        print(f"  HTTP {resp.status_code} · body={len(resp.text)} bytes · final URL={resp.url}")
    except Exception as exc:
        print(f"  ✗ error de red: {exc!r}")
        return

    if resp.status_code != 200:
        return

    soup = BeautifulSoup(resp.text, "lxml")

    # Indicios de SPA (si es SPA, el HTML estático no va a tener cards útiles)
    has_next = "__NEXT_DATA__" in resp.text
    has_data_testid = "data-testid" in resp.text
    print(f"  tiene __NEXT_DATA__: {has_next}")
    print(f"  tiene data-testid: {has_data_testid}")

    # Links únicos a autos
    links = set()
    for a in soup.select("a[href*='/ar/usados/']"):
        href = a.get("href", "")
        if re.search(r"/ar/usados/[^/\"\s]+-[a-z0-9]+", href):
            links.add(href)
    print(f"  links únicos tipo auto (con slug): {len(links)}")
    for h in list(links)[:5]:
        print(f"    · {h[:100]}")

    # Si no hay links tipo auto, mostrar qué categorías aparecen
    if not links:
        cats = set()
        for a in soup.select("a[href*='/ar/usados/']"):
            href = a.get("href", "")
            m = re.match(r".*?/ar/usados/([a-z0-9-]+)/?$", href)
            if m:
                cats.add(m.group(1))
        print(f"  ⚠ no hay autos individuales en el HTML (probablemente SPA).")
        print(f"  categorías encontradas: {sorted(cats)[:15]}")
        print(f"  → Kavak se renderiza con JS; scraping estático no alcanza. "
              f"Sólo Mercado Libre está aportando resultados.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Diagnóstico de scrapers")
    ap.add_argument("--model", help="Correr sólo una query de ML (ej: 'fit')")
    ap.add_argument("--skip-kavak", action="store_true", help="No consultar Kavak")
    args = ap.parse_args()

    print(f"User-Agent: {HEADERS['User-Agent']}")
    print(f"MAX_PRICE_USD para URL de ML: {MAX_PRICE_USD}")

    if args.model:
        diagnose_ml_one(f"{args.model} automatico", sample_size=10)
    else:
        diagnose_ml_all()

    if not args.skip_kavak:
        diagnose_kavak()

    banner("FIN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
