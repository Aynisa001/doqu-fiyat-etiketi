"""
doquhome.com.tr üzerindeki "Koltuklar" ve "Yatak & Yatak Pedi" kategori
sayfalarını tarar, oradaki ürün adı + fiyat bilgilerini çıkarır ve
doqu-fiyat-etiketi-v8.html içindeki CATALOG yapısıyla BİREBİR aynı şemada
bir sofa-prices.json dosyası üretir:

{
  "Koltuklar": [
    {"name": "...", "price": 29990, "disc": 24990, "gen": "—", "der": "—", "yuk": "—"},
    ...
  ],
  "Yatak & Yatak Pedi": [ ... ],
  "_meta": {"fetched_at": "2026-08-20T12:00:00Z", "source": "https://www.doquhome.com.tr/..."}
}

Neden regex/metin tabanlı ve BeautifulSoup ile "kart" tespiti?
----------------------------------------------------------------
Sitenin tam HTML/CSS sınıf yapısını göremediğim için (bu betik hiçbir
zaman gerçek tarayıcıda çalıştırılıp doğrulanmadı), en dayanıklı yöntem
olarak her ürün kartını "Sepete Ekle" butonunun bulunduğu en yakın
kapsayıcı eleman üzerinden tespit ediyorum, ardından o kartın metnini
düzleştirip fiyatları regex ile çıkarıyorum. Bu yaklaşım class isimleri
değişse bile büyük ölçüde çalışmaya devam eder, ama YİNE DE İLK
ÇALIŞTIRMADAN SONRA ÇIKTIYI (sofa-prices.json) MUTLAKA KONTROL EDİN.

Kullanım:
    pip install requests beautifulsoup4
    python scripts/fetch_sofa_prices.py > sofa-prices.json
"""
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------
# 1) Taranacak kategori sayfaları ve her birinin CATALOG'daki karşılığı
# --------------------------------------------------------------------------
CATEGORY_PAGES = {
    "Koltuklar": [
        "https://www.doquhome.com.tr/kategori/koltuklar",
    ],
    "Yatak & Yatak Pedi": [
        "https://www.doquhome.com.tr/kategori/yatak-yatak-pedi",
    ],
}

# --------------------------------------------------------------------------
# 2) HTML'deki ürün adı -> doqu-fiyat-etiketi-v8.html CATALOG'undaki isim
#    Site üzerindeki yazım (boşluk, büyük/küçük harf, "x" vs "X") ile
#    HTML'deki CATALOG sabitindeki yazım birebir aynı olmayabiliyor, bu
#    yüzden normalize edilmiş (boşluksuz, küçük harf) isimle eşleştiriyoruz.
#    Yeni bir ürün eklerseniz hem burada hem HTML'deki CATALOG'da aynı
#    normalize edilmiş adı kullandığınızdan emin olun.
# --------------------------------------------------------------------------
def normalize(name: str) -> str:
    """Karşılaştırma için: küçük harfe çevir, Türkçe karakterleri sadeleştir,
    boşluk/tire/nokta gibi ayraçları kaldır."""
    name = name.strip().lower()
    name = name.replace("i̇", "i")  # Türkçe büyük İ -> küçük i sorunlu unicode
    trans = str.maketrans("çğıöşü", "cgiosu")
    name = name.translate(trans)
    name = unicodedata.normalize("NFKD", name)
    name = re.sub(r"[\s\-\._]+", "", name)
    return name


# CATALOG'daki (HTML'deki) isimlerin listesi — buradaki her isim,
# doqu-fiyat-etiketi-v8.html içindeki CATALOG["Koltuklar"] /
# CATALOG["Yatak & Yatak Pedi"] dizisindeki "name" alanıyla BİREBİR aynı
# yazılmalı (Türkçe karakterler dahil). Yeni ürün eklerken buraya da ekleyin.
KNOWN_NAMES = {
    "Koltuklar": [
        "Bohem Modüler Koltuk 200x100 - Füme",
        "Bohem Modüler Koltuk 200x100 - Gümüş",
        "Bohem Modüler Koltuk 200x100 - Kiremit",
        "Bohem Keten İkili Koltuk - Açık Gri",
        "Bohem Uzanmalı Köşe Koltuk - Füme",
        "Bohem Uzanmalı Köşe Koltuk - Gümüş",
        "Bohem Köşe Koltuk - Füme",
        "Bohem Köşe Koltuk - Gümüş",
        "Bohem İkili Koltuk - Füme",
        "Bohem İkili Koltuk - Krem",
    ],
    "Yatak & Yatak Pedi": [
        "Stress Free Sleep Yatak 100x200",
        "Graphene Dream Yatak 100x200",
        "Prosoft Comfort Yatak 90x190",
        "Bamboo Comfort Yatak 90x190",
        "Naturel Hand Made Coco Wool Yatak 100x200",
        "Naturel Hand Made Harmony Yatak 200x200",
        "Naturel At Kılı Yatak Pedi 200x200",
        "Naturel Hand Made Pamuk Yatak Pedi 200x200",
    ],
}

PRICE_RE = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d{2})\s*TL")
BUY_BUTTON_TEXTS = {"sepete ekle", "sepete\u00a0ekle"}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
}



def tl_to_int(tl_str: str) -> int:
    """'24.990,00' -> 24990"""
    return int(round(float(tl_str.replace(".", "").replace(",", "."))))


def find_product_cards(soup: BeautifulSoup):
    """'Sepete Ekle' metnini içeren en küçük mantıklı kapsayıcıyı bulur ve
    o kartın düzleştirilmiş metnini döndürür."""
    cards = []
    for el in soup.find_all(string=re.compile(r"sepete\s*ekle", re.IGNORECASE)):
        node = el.parent
        # Kart genelde buton/linkin birkaç üst ebeveynidir: fiyatı ve
        # ürün adını da içerecek kadar yukarı çık, ama tüm sayfayı
        # yutacak kadar da değil (metin uzunluğuna üst sınır koyarak).
        card_text = ""
        ancestor = node
        for _ in range(6):
            if ancestor is None:
                break
            text = ancestor.get_text(separator=" ", strip=True)
            if PRICE_RE.search(text) and 10 < len(text) < 1500:
                card_text = text
                break
            ancestor = ancestor.parent
        if card_text:
            cards.append(card_text)
    return cards


def parse_card(card_text: str):
    """Bir ürün kartının düzleştirilmiş metninden (isim, fiyat, indirimli
    fiyat) çıkarır. Örnek girdi:
    '%21 Bohem Modüler Koltuk 200 X 100 cm - Füme %21İndirim 13.990,00 TL
     10.990,00 TL Sepete Ekle ● 28 cm Yükseklik ...'
    """
    prices = PRICE_RE.findall(card_text)
    if not prices:
        return None
    first_price_pos = card_text.find(prices[0])
    name_part = card_text[:first_price_pos]
    # Baştaki "%21" veya sondaki "%21İndirim" gibi indirim rozetlerini temizle
    name_part = re.sub(r"%\s*\d+\s*İndirim", " ", name_part, flags=re.IGNORECASE)
    name_part = re.sub(r"^%\s*\d+\s*", "", name_part)
    name_part = re.sub(r"\s+", " ", name_part).strip(" -·")
    if not name_part:
        return None

    if len(prices) >= 2:
        list_price = tl_to_int(prices[0])
        disc_price = tl_to_int(prices[1])
        if disc_price >= list_price:
            # Beklenmedik sıralama; güvenli tarafta kal, indirim yok say
            return name_part, list_price, None
        return name_part, list_price, disc_price
    else:
        return name_part, tl_to_int(prices[0]), None


def fetch_category(url: str):
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    return find_product_cards(soup)


def build_lookup(cat_key: str):
    return {normalize(n): n for n in KNOWN_NAMES[cat_key]}


def main():
    result = {}
    unmatched = []

    for cat_key, urls in CATEGORY_PAGES.items():
        lookup = build_lookup(cat_key)
        found = {}  # canonical_name -> (price, disc)
        for url in urls:
            try:
                cards = fetch_category(url)
            except Exception as exc:  # noqa: BLE001
                print(f"UYARI: {url} çekilemedi: {exc}", file=sys.stderr)
                continue
            for card_text in cards:
                parsed = parse_card(card_text)
                if not parsed:
                    continue
                raw_name, price, disc = parsed
                key = normalize(raw_name)
                # Normalize edilmiş isim, bilinen isimlerden biriyle TAM
                # eşleşmiyorsa, en uzun ortak önek/kapsama ile dene (site
                # üzerindeki isim CATALOG'dakinden biraz daha uzun/kısa
                # olabilir, örn. sonunda "cm" veya renk sırası farkı).
                match = lookup.get(key)
                if not match:
                    for norm_known, canonical in lookup.items():
                        if norm_known in key or key in norm_known:
                            match = canonical
                            break
                if match:
                    found[match] = (price, disc)
                else:
                    unmatched.append(f"[{cat_key}] {raw_name}")

        items = []
        for canonical in KNOWN_NAMES[cat_key]:
            if canonical in found:
                price, disc = found[canonical]
                items.append(
                    {
                        "name": canonical,
                        "price": price,
                        "disc": disc,
                        "gen": "—",
                        "der": "—",
                        "yuk": "—",
                    }
                )
            else:
                print(
                    f"UYARI: '{canonical}' ({cat_key}) sitede bulunamadı, "
                    f"eski fiyat HTML'deki dahili listede kalmaya devam edecek.",
                    file=sys.stderr,
                )
        result[cat_key] = items

    result["_meta"] = {
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": list(CATEGORY_PAGES.values()),
    }

    if unmatched:
        print(
            "NOT: Sitede bulunup CATALOG'da karşılığı olmayan ürünler "
            "(bunlar için scripts/fetch_sofa_prices.py içindeki KNOWN_NAMES "
            "listesini güncelleyebilirsiniz):",
            file=sys.stderr,
        )
        for u in unmatched:
            print(f"  - {u}", file=sys.stderr)

    print(json.dumps(result, ensure_ascii=False, indent=2))
if __name__ == "__main__":
    main()
