import os
import json
import time
from typing import Dict, Any, List
from pathlib import Path

import requests


STORAGE = Path("/root/tg_autoposter/market_storage.json")
_CRYPTO_CACHE: Dict[str, Any] = {"ts": 0, "data": {}}


def _load_storage() -> Dict[str, Any]:
    if not STORAGE.exists():
        return {}
    try:
        return json.loads(STORAGE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_storage(data: Dict[str, Any]) -> None:
    STORAGE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _now() -> int:
    return int(time.time())


def _get_state(data: Dict[str, Any]) -> Dict[str, Any]:
    return data.setdefault("market_state", {})


def _get_last_post_ts(state: Dict[str, Any], key: str) -> int:
    return int(state.get("last_post_ts", {}).get(key, 0))


def _set_last_post_ts(state: Dict[str, Any], key: str, ts: int) -> None:
    state.setdefault("last_post_ts", {})[key] = ts


def _get_last_price(state: Dict[str, Any], key: str) -> float | None:
    v = state.get("last_price", {}).get(key)
    return float(v) if v is not None else None


def _set_last_price(state: Dict[str, Any], key: str, price: float) -> None:
    state.setdefault("last_price", {})[key] = price


def _pct_change(old: float, new: float) -> float:
    return ((new - old) / old) * 100.0


# -------------------- PRICE SOURCES --------------------

def get_binance_price(symbol: str) -> float:
    """
    Источник крипты без ключей.
    Binance -> 451, CoinGecko -> 429, CoinCap -> DNS fail.
    Берём цены через Kraken public API.
    """
    global _CRYPTO_CACHE

    # кэш 45 секунд
    if _CRYPTO_CACHE["data"] and (_now() - int(_CRYPTO_CACHE["ts"]) < 45):
        cached = _CRYPTO_CACHE["data"].get(symbol)
        if cached is not None:
            return float(cached)

    pair_map = {
        "BTCUSDT": "XBTUSD",
        "ETHUSDT": "ETHUSD",
    }
    pair = pair_map.get(symbol)
    if not pair:
        raise ValueError(f"Unsupported symbol: {symbol}")

    url = "https://api.kraken.com/0/public/Ticker"
    r = requests.get(url, params={"pair": pair}, timeout=10)
    r.raise_for_status()
    j = r.json()

    if j.get("error"):
        raise RuntimeError(f"Kraken error: {j['error']}")

    # Kraken возвращает объект с ключом пары (иногда отличается), берём первый
    result = j["result"]
    first_key = next(iter(result.keys()))
    last_price = float(result[first_key]["c"][0])  # "c" = last trade [price, lot volume]

    _CRYPTO_CACHE["ts"] = _now()
    _CRYPTO_CACHE["data"][symbol] = last_price
    return last_price



def get_cbr_rates() -> Dict[str, float]:
    # USD, EUR to RUB (official)
    url = "https://www.cbr-xml-daily.ru/daily_json.js"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    j = r.json()
    usd = float(j["Valute"]["USD"]["Value"])
    eur = float(j["Valute"]["EUR"]["Value"])
    return {"USDRUB": usd, "EURRUB": eur}


def get_moex_imoex() -> float:
    # IMOEX index last
    url = "https://iss.moex.com/iss/engines/stock/markets/index/boards/SNDX/securities/IMOEX.json?iss.meta=off"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    j = r.json()
    cols = j["securities"]["columns"]
    data = j["securities"]["data"][0]
    row = dict(zip(cols, data))
    # try LAST, fallback to PREVPRICE
    v = row.get("LAST") or row.get("PREVPRICE")
    return float(v)


# -------------------- TRIGGERS --------------------

def check_market_triggers() -> List[Dict[str, Any]]:
    """
    Returns list of events to post:
    {"key": "BTCUSDT", "title": "...", "price": ..., "pct": ..., "reason": "..."}
    """
    cooldown_sec = int(os.getenv("MARKET_COOLDOWN_SEC", "21600"))  # 6h

    # thresholds
    crypto_hour = float(os.getenv("TH_CRYPTO_1H", "2.0"))
    crypto_day = float(os.getenv("TH_CRYPTO_24H", "5.0"))
    fx_day = float(os.getenv("TH_FX_24H", "1.5"))
    moex_day = float(os.getenv("TH_MOEX_24H", "2.0"))

    data = _load_storage()
    state = _get_state(data)

    events: List[Dict[str, Any]] = []

        # --- Crypto (BTC/ETH) ---
    for sym in ["BTCUSDT", "ETHUSDT"]:
        try:
            price = float(get_binance_price(sym))
            print(f"✅ CRYPTO OK {sym} price={price}")
        except Exception as e:
            print(f"⚠️ CRYPTO FETCH FAIL {sym}: {repr(e)}")
            continue

        last = _get_last_price(state, sym)
        _set_last_price(state, sym, price)

        # если это первый замер — просто сохраняем цену
        if last is None:
            continue

        pct = _pct_change(last, price)
        if abs(pct) >= crypto_hour:
            last_post = _get_last_post_ts(state, sym)
            if _now() - last_post >= cooldown_sec:
                _set_last_post_ts(state, sym, _now())
                events.append({
                    "key": sym,
                    "asset": sym.replace("USDT", ""),
                    "market": "crypto",
                    "price": price,
                    "pct": pct,
                    "threshold": crypto_hour,
                })

    # --- FX (CBR daily rate, so day-window) ---
    try:
        rates = get_cbr_rates()
    except Exception:
        rates = {}

    for sym in ["USDRUB", "EURRUB"]:
        if sym not in rates:
            continue

        price = float(rates[sym])
        last = _get_last_price(state, sym)
        _set_last_price(state, sym, price)

        if last is None:
            continue

        pct = _pct_change(last, price)
        if abs(pct) >= fx_day:
            last_post = _get_last_post_ts(state, sym)
            if _now() - last_post >= cooldown_sec:
                _set_last_post_ts(state, sym, _now())
                events.append({
                    "key": sym,
                    "asset": sym[:3],
                    "market": "fx",
                    "price": price,
                    "pct": pct,
                    "threshold": fx_day,
                })

    # --- MOEX ---
    try:
        imoex = get_moex_imoex()
    except Exception:
        imoex = None

    if imoex is not None:
        sym = "IMOEX"
        last = _get_last_price(state, sym)
        _set_last_price(state, sym, float(imoex))

        if last is not None:
            pct = _pct_change(last, float(imoex))
            if abs(pct) >= moex_day:
                last_post = _get_last_post_ts(state, sym)
                if _now() - last_post >= cooldown_sec:
                    _set_last_post_ts(state, sym, _now())
                    events.append({
                        "key": sym,
                        "asset": "IMOEX",
                        "market": "ru",
                        "price": float(imoex),
                        "pct": pct,
                        "threshold": moex_day,
                    })
    print(state.get("last_price", {}))
    _save_storage(data)
    print(data.get("market_state", {}).get("last_price", {}))
    return events

def update_market_ticks() -> None:
    """
    Совместимость с планом: обновляет цены/состояние.
    Фактически — прогоняет check_market_triggers() без публикации.
    """
    check_market_triggers()


def check_market_alerts() -> Dict[str, str]:
    """
    Возвращает готовые сообщения для постинга: {event_key: text}
    """
    events = check_market_triggers()
    alerts: Dict[str, str] = {}

    for e in events:
        asset = e.get("asset", e["key"])
        price = e["price"]
        pct = e["pct"]

        # Форматируем аккуратно под инвест-канал
        direction = "рост" if pct > 0 else "падение"
        pct_str = f"{pct:+.2f}%"

        if e["market"] == "crypto":
            title = f"📉 Рынок: {asset} — резкое движение"
            why = "Крипта часто двигается импульсами: ликвидность, новости, крупные заявки."
        elif e["market"] == "fx":
            title = f"💱 Валюта: {asset}/RUB — заметное изменение"
            why = "Официальный курс ЦБ обновляется раз в день; это индикатор тренда, не минутных колебаний."
        else:
            title = f"📊 РФ рынок: IMOEX — движение индекса"
            why = "Индекс реагирует на ожидания по ставке, отчётности, геополитику и потоки капитала."

        text = (
            f"{title}\n\n"
            f"{direction.capitalize()}: {pct_str}\n"
            f"Цена: {price:.2f}\n\n"
            f"{why}\n\n"
            f"#рынки #инвестиции"
        )
        alerts[e["key"]] = text

    return alerts
