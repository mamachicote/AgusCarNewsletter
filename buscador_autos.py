"""
buscador_autos.py
=================

Busca autos en Mercado Libre Argentina y Kavak Argentina, filtra según
criterios (compactos automáticos, hasta USD 18.000, hasta 100.000 km,
año mínimo 2012, zona CABA/Buenos Aires con excepción para los 3 mejores
fuera de AMBA), enriquece con benchmarks de confiabilidad y manda un
email HTML con las oportunidades nuevas del día.

Pensado para correr diariamente en GitHub Actions.

Requiere las variables de entorno:
  - SENDER_EMAIL:    cuenta de Gmail que envía el mail
  - SENDER_PASSWORD: App Password de Gmail (no la contraseña normal)
  - HISTORY_FILE:    (opcional) path al JSON con IDs ya enviados
                     (default "sent_cars.json")

El destinatario está hardcodeado en DESTINATARIO.
"""

from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
import sys
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

MAX_PRICE_USD = 18_000
MAX_KM = 100_000
MIN_YEAR = 2012

DESTINATARIO = "agustina.machicote@gmail.com"

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.7",
}

HTTP_TIMEOUT = 20

AMBA_KEYWORDS = [
    "capital federal",
    "caba",
    "ciudad autónoma",
    "ciudad autonoma",
    "buenos aires",
    "gba",
    "bs as",
    "bs. as.",
    "pcia de buenos aires",
    "provincia de buenos aires",
]


BENCHMARKS: Dict[str, Dict] = {
    "toyota etios":     {"score": 9.1, "recomendado": True,  "consumo": "6.5 L/100 km", "mantenimiento": "Bajo",
                         "pros": ["Confiabilidad Toyota", "Repuestos accesibles", "Bajo consumo"],
                         "cons": ["Terminaciones básicas", "Aislamiento acústico regular"]},
    "honda fit":        {"score": 8.8, "recomendado": True,  "consumo": "6.8 L/100 km", "mantenimiento": "Medio",
                         "pros": ["Espacio interior sobresaliente", "Mecánica robusta", "Buena reventa"],
                         "cons": ["Repuestos algo más caros", "Suspensión firme"]},
    "suzuki vitara":    {"score": 8.7, "recomendado": True,  "consumo": "7.2 L/100 km", "mantenimiento": "Bajo",
                         "pros": ["Altura generosa", "Buen comportamiento urbano", "Bajo consumo"],
                         "cons": ["Interior ajustado atrás", "Red de concesionarios limitada"]},
    "chevrolet onix":   {"score": 8.6, "recomendado": True,  "consumo": "6.9 L/100 km", "mantenimiento": "Medio",
                         "pros": ["Equipamiento completo", "Manejo cómodo", "Repuestos accesibles"],
                         "cons": ["Algunos reportes de electrónica", "Reventa moderada"]},
    "honda city":       {"score": 8.5, "recomendado": True,  "consumo": "6.7 L/100 km", "mantenimiento": "Medio",
                         "pros": ["Sedán cómodo y espacioso", "Motor suave", "Buen valor de reventa"],
                         "cons": ["Precios más altos que rivales", "Terminaciones discretas"]},
    "volkswagen polo":  {"score": 8.4, "recomendado": True,  "consumo": "7.0 L/100 km", "mantenimiento": "Medio",
                         "pros": ["Andar sólido", "Seguridad activa", "Interior prolijo"],
                         "cons": ["Mantenimiento programado algo caro"]},
    "peugeot 208":      {"score": 8.0, "recomendado": True,  "consumo": "7.1 L/100 km", "mantenimiento": "Medio",
                         "pros": ["Diseño atractivo", "Manejo ágil", "Buena insonorización"],
                         "cons": ["Electrónica sensible", "Repuestos no siempre rápidos"]},
    "renault sandero":  {"score": 7.9, "recomendado": True,  "consumo": "7.3 L/100 km", "mantenimiento": "Bajo",
                         "pros": ["Repuestos baratos", "Baúl generoso"],
                         "cons": ["Terminaciones simples", "Reventa moderada"]},
    "volkswagen gol":   {"score": 7.8, "recomendado": False, "consumo": "7.5 L/100 km", "mantenimiento": "Bajo",
                         "pros": ["Ampliamente conocido", "Repuestos accesibles"],
                         "cons": ["Plataforma antigua", "Seguridad activa limitada en versiones básicas"]},
    "fiat argo":        {"score": 7.5, "recomendado": False, "consumo": "7.2 L/100 km", "mantenimiento": "Medio",
                         "pros": ["Interior moderno", "Tecnología destacada"],
                         "cons": ["Reportes de fiabilidad mixtos", "Valor de reventa bajo"]},
    "ford ka":          {"score": 7.2, "recomendado": False, "consumo": "7.4 L/100 km", "mantenimiento": "Medio",
                         "pros": ["Económico de comprar", "Ágil en ciudad"],
                         "cons": ["Motor algo ruidoso", "Reportes de problemas en caja automática"]},
}


# ---------------------------------------------------------------------------
# Modelo de datos
# ---------------------------------------------------------------------------

@dataclass
class Car:
    id: str
    source: str            # "mercadolibre" | "kavak"
    title: str
    price_usd: Optional[int]
    km: Optional[int]
    year: Optional[int]
    location: str
    url: str
    image: Optional[str] = None
    model_key: Optional[str] = None
    benchmark: Optional[Dict] = None
    score: Optional[float] = None
    in_amba: bool = False
    outside_amba_highlight: bool = False
    raw: Dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Utilidades de parsing
# ---------------------------------------------------------------------------

_NON_DIGIT = re.compile(r"[^\d]")


def _only_digits(text: str) -> str:
    return _NON_DIGIT.sub("", text or "")


def parse_int(text: Optional[str]) -> Optional[int]:
    if not text:
        return None
    digits = _only_digits(text)
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def format_miles_ar(n: Optional[int]) -> str:
    """Formatea un entero con punto como separador de miles (convención AR)."""
    if n is None:
        return "—"
    s = f"{int(n):,}"
    return s.replace(",", ".")


def detect_model_key(title: str) -> Optional[str]:
    t = (title or "").lower()
    for key in BENCHMARKS:
        if key in t:
            return key
    return None


def is_in_amba(location: str) -> bool:
    loc = (location or "").lower()
    return any(kw in loc for kw in AMBA_KEYWORDS)


def extract_year(text: str) -> Optional[int]:
    """Busca un año plausible (2000-2030) en un texto."""
    if not text:
        return None
    for m in re.finditer(r"(19|20)\d{2}", text):
        y = int(m.group(0))
        if 2000 <= y <= 2030:
            return y
    return None


def extract_km(text: str) -> Optional[int]:
    """Busca '<n> km' en un texto."""
    if not text:
        return None
    m = re.search(r"([\d\.\,]+)\s*km", text, re.IGNORECASE)
    if not m:
        return None
    return parse_int(m.group(1))


def extract_mla_id(url: str) -> Optional[str]:
    if not url:
        return None
    m = re.search(r"(MLA[-\s]?\d+)", url, re.IGNORECASE)
    if not m:
        return None
    return m.group(1).upper().replace("-", "").replace(" ", "")


# ---------------------------------------------------------------------------
# Scraping: Mercado Libre
# ---------------------------------------------------------------------------

def _build_ml_url(query: str) -> str:
    q = quote_plus(query).replace("+", "-")
    return (
        f"https://autos.mercadolibre.com.ar/{q}"
        f"_PriceRange_0USD-{MAX_PRICE_USD}USD_NoIndex_True"
    )


def _parse_ml_card(card) -> Optional[Car]:
    # Título + link
    a = card.select_one("a.poly-component__title") or card.select_one("h2 a") or card.select_one("a")
    if not a:
        return None
    title = (a.get_text(strip=True) or "").strip()
    url = a.get("href", "").strip()
    if not title or not url:
        return None

    # Precio: priorizamos USD. Descartamos si no lo encontramos en USD.
    price_usd: Optional[int] = None
    price_block = card.select_one(".poly-price__current") or card.select_one(".ui-search-price") or card
    if price_block:
        # Buscamos un fragmento que tenga "US$" o currency="USD"
        symbol = price_block.select_one(".andes-money-amount__currency-symbol")
        fraction = price_block.select_one(".andes-money-amount__fraction")
        sym_text = (symbol.get_text(strip=True) if symbol else "").upper()
        if fraction and "US" in sym_text:
            price_usd = parse_int(fraction.get_text(strip=True))
        else:
            # Fallback: escaneamos todo el bloque buscando "US$ 12.345"
            txt = price_block.get_text(" ", strip=True)
            m = re.search(r"US\$\s*([\d\.\,]+)", txt)
            if m:
                price_usd = parse_int(m.group(1))

    if price_usd is None:
        # No está en USD — descartamos.
        return None

    # Atributos (año, km) — vienen en <ul class="poly-attributes_list"> o similar
    attrs_text = ""
    for sel in [".poly-attributes_list", ".ui-search-card-attributes", ".poly-component__attributes-list"]:
        node = card.select_one(sel)
        if node:
            attrs_text = node.get_text(" · ", strip=True)
            break
    if not attrs_text:
        # Último recurso: todo el texto de la card
        attrs_text = card.get_text(" · ", strip=True)

    year = extract_year(attrs_text)
    km = extract_km(attrs_text)

    # Ubicación
    location = ""
    loc_node = (
        card.select_one(".poly-component__location")
        or card.select_one(".ui-search-item__location")
        or card.select_one(".ui-search-item__group__element--location")
    )
    if loc_node:
        location = loc_node.get_text(" ", strip=True)

    # Imagen
    image = None
    img = card.select_one("img")
    if img:
        image = img.get("data-src") or img.get("src")

    car_id = extract_mla_id(url) or url
    return Car(
        id=f"ML-{car_id}",
        source="mercadolibre",
        title=title,
        price_usd=price_usd,
        km=km,
        year=year,
        location=location,
        url=url,
        image=image,
    )


def scrape_mercadolibre() -> List[Car]:
    """Scrapea una query por modelo. Descarta todo lo que no sea USD."""
    results: List[Car] = []
    seen_ids: set = set()

    for model_key in BENCHMARKS:
        query = f"{model_key} automatico"
        url = _build_ml_url(query)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=HTTP_TIMEOUT)
            if resp.status_code != 200:
                print(f"[ML] {query}: HTTP {resp.status_code}")
                continue
            soup = BeautifulSoup(resp.text, "lxml")
        except Exception as exc:
            print(f"[ML] {query}: error {exc!r}")
            continue

        cards = soup.select("li.ui-search-layout__item, div.ui-search-result__wrapper, div.andes-card.poly-card")
        count = 0
        for card in cards:
            try:
                car = _parse_ml_card(card)
            except Exception as exc:
                print(f"[ML] parse error: {exc!r}")
                continue
            if not car:
                continue
            if car.id in seen_ids:
                continue
            seen_ids.add(car.id)
            results.append(car)
            count += 1
        print(f"[ML] {query}: {count} listings en USD")

    print(f"[ML] Total: {len(results)}")
    return results


# ---------------------------------------------------------------------------
# Scraping: Kavak
# ---------------------------------------------------------------------------

KAVAK_API = (
    "https://www.kavak.com/api/search-api-bff/v1/products"
    "?country=ar&page=1&hitsPerPage=60"
    f"&priceMax={MAX_PRICE_USD}&kmsMax={MAX_KM}"
    "&transmission=Autom%C3%A1tica"
)

KAVAK_HTML = "https://www.kavak.com/ar/usados?transmission=Autom%C3%A1tica"


def _kavak_from_api_item(item: Dict) -> Optional[Car]:
    try:
        title = " ".join(
            str(item.get(k, "")).strip()
            for k in ("make", "model", "trim", "year")
            if item.get(k)
        ).strip()
        if not title:
            title = item.get("name") or item.get("title") or ""
        if not title:
            return None

        price = item.get("price") or item.get("priceUsd") or item.get("price_usd")
        if isinstance(price, dict):
            price = price.get("amount") or price.get("value")
        price_usd = parse_int(str(price)) if price is not None else None

        km = parse_int(str(item.get("kms") or item.get("km") or item.get("mileage") or ""))
        year = item.get("year")
        if isinstance(year, str):
            year = parse_int(year)

        location = (
            item.get("location")
            or item.get("hub")
            or item.get("city")
            or ""
        )
        if isinstance(location, dict):
            location = location.get("name") or location.get("city") or ""

        slug = item.get("slug") or item.get("url") or item.get("permalink") or ""
        if slug and not slug.startswith("http"):
            slug = f"https://www.kavak.com/ar/usados/{slug.lstrip('/')}"
        if not slug:
            slug = KAVAK_HTML

        raw_id = str(item.get("id") or item.get("sku") or item.get("stockId") or slug)
        image = item.get("image") or item.get("mainImage") or None
        if isinstance(image, dict):
            image = image.get("url")
        if isinstance(image, list) and image:
            first = image[0]
            image = first.get("url") if isinstance(first, dict) else first

        return Car(
            id=f"KAVAK-{raw_id}",
            source="kavak",
            title=title,
            price_usd=price_usd,
            km=km,
            year=year if isinstance(year, int) else None,
            location=str(location) if location else "",
            url=slug,
            image=image if isinstance(image, str) else None,
        )
    except Exception as exc:
        print(f"[Kavak API] parse error: {exc!r}")
        return None


def _scrape_kavak_api() -> List[Car]:
    try:
        resp = requests.get(KAVAK_API, headers=HEADERS, timeout=HTTP_TIMEOUT)
        if resp.status_code != 200:
            print(f"[Kavak API] HTTP {resp.status_code}")
            return []
        data = resp.json()
    except Exception as exc:
        print(f"[Kavak API] fallo: {exc!r}")
        return []

    # La respuesta puede venir como {"hits":[...]} o {"data":{"products":[...]}} o lista.
    items = None
    if isinstance(data, dict):
        for key in ("hits", "products", "results", "items"):
            if isinstance(data.get(key), list):
                items = data[key]
                break
        if items is None and isinstance(data.get("data"), dict):
            for key in ("hits", "products", "results", "items"):
                if isinstance(data["data"].get(key), list):
                    items = data["data"][key]
                    break
    elif isinstance(data, list):
        items = data

    if not items:
        print("[Kavak API] respuesta sin items parseables")
        return []

    cars: List[Car] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        car = _kavak_from_api_item(it)
        if car:
            cars.append(car)
    print(f"[Kavak API] {len(cars)} items")
    return cars


def _scrape_kavak_html() -> List[Car]:
    try:
        resp = requests.get(KAVAK_HTML, headers=HEADERS, timeout=HTTP_TIMEOUT)
        if resp.status_code != 200:
            print(f"[Kavak HTML] HTTP {resp.status_code}")
            return []
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception as exc:
        print(f"[Kavak HTML] fallo: {exc!r}")
        return []

    cards = soup.select('[data-testid*="card"], article, a[href*="/ar/usados/"]')
    cars: List[Car] = []
    seen_urls: set = set()

    for card in cards:
        try:
            a = card if card.name == "a" else (card.select_one("a[href*='/ar/usados/']") or card.select_one("a"))
            if not a:
                continue
            href = a.get("href", "")
            if not href or "/ar/usados/" not in href:
                continue
            if href.startswith("/"):
                href = f"https://www.kavak.com{href}"
            if href in seen_urls:
                continue
            seen_urls.add(href)

            title = (a.get_text(" ", strip=True) or "").strip()
            block = card.get_text(" · ", strip=True)

            # Precio — probamos USD primero, si no hay lo descartamos.
            price_usd = None
            m = re.search(r"US\$\s*([\d\.\,]+)", block)
            if m:
                price_usd = parse_int(m.group(1))
            if price_usd is None:
                # Kavak suele listar en ARS; sin USD explícito, descartamos.
                continue

            km = extract_km(block)
            year = extract_year(title) or extract_year(block)

            img = card.select_one("img")
            image = img.get("src") if img else None

            cars.append(Car(
                id=f"KAVAK-{href}",
                source="kavak",
                title=title,
                price_usd=price_usd,
                km=km,
                year=year,
                location="",
                url=href,
                image=image,
            ))
        except Exception as exc:
            print(f"[Kavak HTML] parse error: {exc!r}")
            continue

    print(f"[Kavak HTML] {len(cars)} items")
    return cars


def scrape_kavak() -> List[Car]:
    cars = _scrape_kavak_api()
    if not cars:
        print("[Kavak] API vacía o falló, intento HTML")
        cars = _scrape_kavak_html()
    # Dedupe por id
    by_id: Dict[str, Car] = {}
    for c in cars:
        by_id[c.id] = c
    result = list(by_id.values())
    print(f"[Kavak] Total: {len(result)}")
    return result


# ---------------------------------------------------------------------------
# Filtrado y scoring
# ---------------------------------------------------------------------------

def filter_and_score(cars: List[Car]) -> List[Car]:
    seen_ids: set = set()
    result: List[Car] = []

    for c in cars:
        if c.id in seen_ids:
            continue
        seen_ids.add(c.id)

        c.model_key = detect_model_key(c.title)
        if c.model_key is None:
            continue
        if c.price_usd is None or c.price_usd > MAX_PRICE_USD:
            continue
        if c.km is not None and c.km > MAX_KM:
            continue
        if c.km is None:
            # Sin km es sospechoso; lo dejamos pasar igual para no perder oportunidades.
            pass
        if c.year is not None and c.year < MIN_YEAR:
            continue

        bench = BENCHMARKS.get(c.model_key)
        c.benchmark = bench
        c.score = bench["score"] if bench else None
        c.in_amba = is_in_amba(c.location)
        result.append(c)

    print(f"[Filter] {len(result)} autos cumplen criterios básicos")
    return result


def apply_amba_rule(cars: List[Car]) -> List[Car]:
    in_amba = [c for c in cars if c.in_amba]
    out_amba = [c for c in cars if not c.in_amba]

    out_amba.sort(key=lambda c: (c.score or 0.0), reverse=True)
    top_out = out_amba[:3]
    for c in top_out:
        c.outside_amba_highlight = True

    result = in_amba + top_out
    print(f"[AMBA rule] {len(in_amba)} en AMBA + {len(top_out)} destacados fuera de AMBA = {len(result)}")
    return result


# ---------------------------------------------------------------------------
# Historial (dedupe entre corridas)
# ---------------------------------------------------------------------------

def load_history(path: str) -> set:
    if not os.path.exists(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return set(str(x) for x in data)
    except Exception as exc:
        print(f"[History] no se pudo leer {path}: {exc!r}")
    return set()


def save_history(path: str, ids: set) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sorted(ids), f, ensure_ascii=False, indent=2)
        print(f"[History] guardado {len(ids)} IDs en {path}")
    except Exception as exc:
        print(f"[History] error al guardar {path}: {exc!r}")


# ---------------------------------------------------------------------------
# Render HTML
# ---------------------------------------------------------------------------

def _badge(text: str, bg: str, color: str) -> str:
    return (
        f'<span style="display:inline-block;padding:4px 10px;margin:0 6px 6px 0;'
        f'border-radius:999px;font-size:12px;font-weight:600;'
        f'background:{bg};color:{color};">{text}</span>'
    )


def _car_card_html(car: Car) -> str:
    bench = car.benchmark or {}
    price = f"USD {format_miles_ar(car.price_usd)}" if car.price_usd is not None else "USD —"
    km = f"{format_miles_ar(car.km)} km" if car.km is not None else "km s/d"
    year = str(car.year) if car.year else "año s/d"
    loc = car.location or "Ubicación s/d"
    meta = f"{price} · {km} · {year} · {loc}"

    badges = []
    if bench.get("recomendado"):
        badges.append(_badge("✅ Recomendado", "#dcfce7", "#166534"))
    else:
        badges.append(_badge("⚠️ Con reservas", "#fee2e2", "#991b1b"))
    if car.outside_amba_highlight:
        badges.append(_badge("📍 Fuera de AMBA · Oportunidad destacada", "#ffedd5", "#9a3412"))
    badges_html = "".join(badges)

    if car.source == "mercadolibre":
        btn_bg = "#2563eb"
        btn_text = "Ver en Mercado Libre"
    else:
        btn_bg = "#7c3aed"
        btn_text = "Ver en Kavak"

    button_html = (
        f'<a href="{car.url}" style="display:inline-block;margin-top:10px;'
        f'padding:10px 18px;background:{btn_bg};color:#ffffff;text-decoration:none;'
        f'border-radius:8px;font-weight:600;font-size:14px;">{btn_text}</a>'
    )

    pros_html = "".join(f"<li>{p}</li>" for p in bench.get("pros", []))
    cons_html = "".join(f"<li>{c}</li>" for c in bench.get("cons", []))
    score_txt = f"{bench.get('score', '—')}/10"
    consumo = bench.get("consumo", "—")
    mant = bench.get("mantenimiento", "—")
    model_label = (car.model_key or "—").title()

    benchmark_block = f"""
    <div style="margin-top:14px;padding:14px;background:#f9fafb;border-radius:10px;
                border:1px solid #e5e7eb;font-size:13px;color:#374151;">
      <div style="margin-bottom:6px;"><strong>Modelo:</strong> {model_label} ·
          <strong>Score:</strong> {score_txt} ·
          <strong>Consumo:</strong> {consumo} ·
          <strong>Mantenimiento:</strong> {mant}</div>
      <div style="display:flex;flex-wrap:wrap;gap:16px;margin-top:8px;">
        <div style="min-width:220px;flex:1;">
          <strong style="color:#166534;">Pros</strong>
          <ul style="margin:6px 0 0 18px;padding:0;">{pros_html}</ul>
        </div>
        <div style="min-width:220px;flex:1;">
          <strong style="color:#991b1b;">Contras</strong>
          <ul style="margin:6px 0 0 18px;padding:0;">{cons_html}</ul>
        </div>
      </div>
    </div>
    """

    image_html = ""
    if car.image:
        image_html = (
            f'<img src="{car.image}" alt="" '
            f'style="width:100%;max-height:220px;object-fit:cover;'
            f'border-radius:10px;margin-bottom:12px;" />'
        )

    return f"""
    <div style="border:1px solid #e5e7eb;border-radius:14px;padding:18px;margin:0 0 18px 0;
                background:#ffffff;box-shadow:0 1px 2px rgba(0,0,0,0.04);">
      {image_html}
      <div style="margin-bottom:8px;">{badges_html}</div>
      <div style="font-size:17px;font-weight:700;color:#111827;line-height:1.3;">
          {car.title}
      </div>
      <div style="margin-top:6px;color:#374151;font-size:14px;">{meta}</div>
      {button_html}
      {benchmark_block}
    </div>
    """


def _summary_table_html(cars: List[Car]) -> str:
    counts: Dict[str, int] = {}
    for c in cars:
        if c.model_key:
            counts[c.model_key] = counts.get(c.model_key, 0) + 1

    if not counts:
        return ""

    rows_data: List[Tuple[str, Dict, int]] = []
    for model_key, cnt in counts.items():
        bench = BENCHMARKS.get(model_key, {})
        rows_data.append((model_key, bench, cnt))
    rows_data.sort(key=lambda t: t[1].get("score", 0), reverse=True)
    rows_data = rows_data[:6]

    rows_html = ""
    for model_key, bench, cnt in rows_data:
        rec = "✅" if bench.get("recomendado") else "❌"
        rows_html += f"""
        <tr>
          <td style="padding:8px 10px;border-bottom:1px solid #e5e7eb;">{model_key.title()}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #e5e7eb;">{bench.get('score', '—')}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #e5e7eb;">{rec}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #e5e7eb;">{cnt}</td>
        </tr>
        """

    return f"""
    <div style="margin-top:24px;padding:18px;background:#ffffff;border:1px solid #e5e7eb;
                border-radius:14px;">
      <div style="font-size:16px;font-weight:700;color:#111827;margin-bottom:10px;">
        📊 Top modelos hoy (por score)
      </div>
      <table style="width:100%;border-collapse:collapse;font-size:13px;color:#374151;">
        <thead>
          <tr style="background:#f3f4f6;text-align:left;">
            <th style="padding:8px 10px;">Modelo</th>
            <th style="padding:8px 10px;">Score</th>
            <th style="padding:8px 10px;">Recomendado</th>
            <th style="padding:8px 10px;">Resultados hoy</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
    </div>
    """


def _empty_card_html() -> str:
    return """
    <div style="border:1px dashed #d1d5db;border-radius:14px;padding:24px;margin:0 0 18px 0;
                background:#ffffff;text-align:center;color:#6b7280;">
      <div style="font-size:28px;margin-bottom:6px;">🕵️‍♀️</div>
      <div style="font-size:16px;font-weight:600;color:#111827;">Sin novedades hoy</div>
      <div style="font-size:13px;margin-top:6px;">
        No aparecieron autos nuevos que cumplan los criterios.
        Mañana volvemos a mirar.
      </div>
    </div>
    """


def render_email_html(new_cars: List[Car], all_today: List[Car]) -> str:
    new_cars_sorted = sorted(new_cars, key=lambda c: (c.score or 0), reverse=True)
    cards_html = "".join(_car_card_html(c) for c in new_cars_sorted) if new_cars_sorted else _empty_card_html()
    table_html = _summary_table_html(all_today)
    today_str = datetime.now().strftime("%d/%m/%Y")

    return f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="utf-8"><title>Buscador de autos</title></head>
<body style="margin:0;padding:0;background:#f3f4f6;
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Oxygen,Ubuntu,sans-serif;">
  <div style="max-width:720px;margin:0 auto;padding:24px 16px;">
    <div style="margin-bottom:16px;">
      <div style="font-size:22px;font-weight:800;color:#111827;">🚗 Buscador de autos</div>
      <div style="font-size:13px;color:#6b7280;margin-top:4px;">
        Oportunidades del {today_str} · Compactos automáticos hasta USD {format_miles_ar(MAX_PRICE_USD)}
      </div>
    </div>
    {cards_html}
    {table_html}
    <div style="margin-top:20px;font-size:12px;color:#9ca3af;text-align:center;">
      Fuentes: Mercado Libre Argentina y Kavak Argentina · Corrida automática diaria.
    </div>
  </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def send_email(subject: str, html_body: str) -> None:
    sender = os.environ.get("SENDER_EMAIL")
    password = os.environ.get("SENDER_PASSWORD")
    if not sender or not password:
        raise RuntimeError("Faltan SENDER_EMAIL / SENDER_PASSWORD en el entorno")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = DESTINATARIO
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        server.login(sender, password)
        server.sendmail(sender, [DESTINATARIO], msg.as_string())
    print(f"[Email] enviado a {DESTINATARIO}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    history_file = os.environ.get("HISTORY_FILE", "sent_cars.json")
    print(f"[Run] inicio {datetime.now().isoformat()} · history={history_file}")

    try:
        ml_cars = scrape_mercadolibre()
    except Exception as exc:
        print(f"[ML] fallo general: {exc!r}")
        traceback.print_exc()
        ml_cars = []

    try:
        kavak_cars = scrape_kavak()
    except Exception as exc:
        print(f"[Kavak] fallo general: {exc!r}")
        traceback.print_exc()
        kavak_cars = []

    all_cars = ml_cars + kavak_cars
    print(f"[Run] scrapeados: {len(all_cars)}")

    filtered = filter_and_score(all_cars)
    today = apply_amba_rule(filtered)

    history = load_history(history_file)
    new_cars = [c for c in today if c.id not in history]
    print(f"[Run] hoy={len(today)} · nuevos={len(new_cars)}")

    subject = f"🚗 Buscador de autos · {len(new_cars)} oportunidades ({datetime.now().strftime('%d/%m')})"
    html = render_email_html(new_cars, today)

    try:
        send_email(subject, html)
    except Exception as exc:
        print(f"[Email] fallo: {exc!r}")
        traceback.print_exc()
        return 1

    new_ids = {c.id for c in new_cars}
    save_history(history_file, history | new_ids)
    print(f"[Run] fin {datetime.now().isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
