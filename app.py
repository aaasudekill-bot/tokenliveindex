import os
from flask import Flask, render_template, request, jsonify
import requests
from flask_caching import Cache

app = Flask(__name__)

# ==========================================
# 1. KONFIGURASI API KEY (WAJIB DIISI)
# ==========================================
COINGECKO_API_KEY = "CG-5q1PNRDHjFvyNPyx4RdZjJkb"

# ==========================================
# 2. KONFIGURASI CACHE
# ==========================================
app.config['CACHE_TYPE'] = 'SimpleCache'
app.config['CACHE_DEFAULT_TIMEOUT'] = 300
cache = Cache(app)

# ==========================================
# 3. ROUTE HALAMAN
# ==========================================
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/prediction')
def prediction():
    return render_template('prediction.html')

@app.route('/patterns')
def patterns():
    return render_template('patterns.html')

@app.route('/ai-prediction')
def ai_prediction():
    return render_template('ai_prediction.html')

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

# ==========================================
# 4. HELPER: KONVERSI MATA UANG
# ==========================================
def get_exchange_rate(target_fiat):
    if target_fiat == 'usd':
        return 1.0
    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids=usd&vs_currencies={target_fiat}"
        headers = {"x-cg-demo-api-key": COINGECKO_API_KEY}
        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code == 200:
            data = res.json()
            if 'usd' in data and target_fiat in data['usd']:
                return data['usd'][target_fiat]
    except Exception:
        pass
    return None

# ==========================================
# 5. API: KALKULATOR KONVERSI
# ==========================================
@app.route('/api/convert', methods=['GET'])
def convert():
    crypto_id = request.args.get('crypto_id')
    fiat_currency = request.args.get('fiat_currency', 'usd')
    amount = request.args.get('amount', '1')

    try:
        amount = float(amount)
    except ValueError:
        return jsonify({"status": "error", "message": "Invalid amount"})

    url = f"https://api.coingecko.com/api/v3/simple/price?ids={crypto_id}&vs_currencies={fiat_currency}"
    headers = {"x-cg-demo-api-key": COINGECKO_API_KEY}

    try:
        response = requests.get(url, headers=headers, timeout=8)
        data = response.json()
        if crypto_id in data and fiat_currency in data[crypto_id]:
            price = data[crypto_id][fiat_currency]
            return jsonify({
                "status": "success",
                "price": price,
                "total": round(float(amount) * price, 2),
                "fiat": fiat_currency.upper()
            })
        else:
            return jsonify({"status": "error", "message": "Asset or currency not recognized"})
    except Exception:
        return jsonify({"status": "error", "message": "Failed to connect to pricing server"})

# ==========================================
# 6. API: DATA GRAFIK HISTORIS (3 TIER FALLBACK)
# ==========================================
@cache.cached(timeout=300, query_string=True)
@app.route('/api/chart', methods=['GET'])
def get_chart_data():
    crypto_id = request.args.get('crypto_id', 'bitcoin')
    days = request.args.get('days', '7')
    fiat_currency = request.args.get('fiat_currency', 'usd')

    base_data = None
    store_name = ""

    # JALUR 1: COINGECKO (TIMEOUT 8 DETIK)
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{crypto_id}/market_chart?vs_currency={fiat_currency}&days={days}"
        headers = {"x-cg-demo-api-key": COINGECKO_API_KEY}
        response = requests.get(url, headers=headers, timeout=8)
        if response.status_code == 200:
            data = response.json()
            if 'prices' in data:
                base_data = [{"time": int(item[0] / 1000), "value": item[1]} for item in data['prices']]
                store_name = "coingecko"
    except Exception:
        pass

    # JALUR 2: FALLBACK COINCAP (TIMEOUT 8 DETIK)
    if not base_data:
        try:
            url_cc = f"https://api.coincap.io/v2/assets/{crypto_id}/history?interval=h1"
            response_cc = requests.get(url_cc, timeout=8)
            if response_cc.status_code == 200:
                data_cc = response_cc.json()
                if 'data' in data_cc and len(data_cc['data']) > 0:
                    base_data = [{"time": int(item['time'] / 1000), "value": float(item['priceUsd'])} for item in data_cc['data']]
                    store_name = "coincap"
        except Exception:
            pass

    # JALUR 3: FALLBACK BINANCE (TIMEOUT 8 DETIK)
    if not base_data:
        binance_symbol_map = {
            "bitcoin": "BTCUSDT", "ethereum": "ETHUSDT", "tether": "USDTUSDC",
            "binancecoin": "BNBUSDT", "solana": "SOLUSDT", "ripple": "XRPUSDT",
            "usd-coin": "USDCUSDT", "cardano": "ADAUSDT", "dogecoin": "DOGEUSDT",
            "tron": "TRXUSDT", "avalanche-2": "AVAXUSDT", "polkadot": "DOTUSDT",
            "chainlink": "LINKUSDT", "toncoin": "TONUSDT", "shiba-inu": "SHIBUSDT",
            "litecoin": "LTCUSDT", "uniswap": "UNIUSDT", "stellar": "XLMUSDT"
        }
        limit_map = {"7": 168, "14": 336, "30": 720, "90": 2160}
        try:
            symbol_bn = binance_symbol_map.get(crypto_id, f"{crypto_id.upper()}USDT")
            limit = limit_map.get(days, 168)
            url_bn = f"https://api.binance.com/api/v3/klines?symbol={symbol_bn}&interval=1h&limit={limit}"
            response_bn = requests.get(url_bn, timeout=8)
            if response_bn.status_code == 200:
                data_bn = response_bn.json()
                if len(data_bn) > 2:
                    base_data = [{"time": int(item[0] / 1000), "value": float(item[4])} for item in data_bn]
                    store_name = "binance"
        except Exception:
            pass

    # KONVERSI USD KE MATA UANG LAIN (JIKA DATA DARI FALLBACK)
    if base_data and fiat_currency != 'usd' and store_name != "coingecko":
        rate = get_exchange_rate(fiat_currency)
        if rate:
            for item in base_data:
                item['value'] = item['value'] * rate
            store_name = f"{store_name} (Converted to {fiat_currency.upper()})"

    if base_data:
        return jsonify({"status": "success", "data": base_data, "source": store_name})
    else:
        return jsonify({"status": "error", "message": "All data sources failed to load"})

# ==========================================
# 7. API: MARKET SENTIMEN (FEAR & GREED)
# ==========================================
@app.route('/api/sentimen')
def get_sentimen():
    url = "https://api.alternative.me/fng/?limit=30"
    try:
        response = requests.get(url, timeout=8)
        data = response.json()
        if 'data' in data:
            return jsonify({
                "status": "success",
                "current": data['data'][0],
                "history": data['data']
            })
        else:
            return jsonify({"status": "error", "message": "Sentiment data unavailable"})
    except Exception:
        return jsonify({"status": "error", "message": "Connection timeout"})

# ==========================================
# 8. API: PIVOT POINTS (SUPPORT & RESISTANCE)
# ==========================================
@app.route('/api/pivot')
def get_pivot_points():
    crypto_id = request.args.get('crypto_id', 'bitcoin')

    binance_symbol_map = {
        "bitcoin": "BTCUSDT", "ethereum": "ETHUSDT", "tether": "USDTUSDC",
        "binancecoin": "BNBUSDT", "solana": "SOLUSDT", "ripple": "XRPUSDT",
        "usd-coin": "USDCUSDT", "cardano": "ADAUSDT", "dogecoin": "DOGEUSDT",
        "tron": "TRXUSDT", "avalanche-2": "AVAXUSDT", "polkadot": "DOTUSDT",
        "chainlink": "LINKUSDT", "toncoin": "TONUSDT", "shiba-inu": "SHIBUSDT",
        "litecoin": "LTCUSDT", "uniswap": "UNIUSDT", "stellar": "XLMUSDT"
    }
    symbol = binance_symbol_map.get(crypto_id, "BTCUSDT")
    url_bn = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1d&limit=1"

    try:
        response_bn = requests.get(url_bn, timeout=8)
        if response_bn.status_code == 200:
            data_bn = response_bn.json()[0]
            high = float(data_bn[2])
            low = float(data_bn[3])
            close = float(data_bn[4])

            pp = (high + low + close) / 3
            r1 = (2 * pp) - low
            s1 = (2 * pp) - high
            r2 = pp + (high - low)
            s2 = pp - (high - low)

            return jsonify({
                "status": "success",
                "data": {
                    "pp": round(pp, 2),
                    "r1": round(r1, 2),
                    "r2": round(r2, 2),
                    "s1": round(s1, 2),
                    "s2": round(s2, 2)
                }
            })
    except Exception:
        pass

    return jsonify({"status": "error", "message": "Failed to calculate pivot points"})

# ==========================================
# 9. START SERVER
# ==========================================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
