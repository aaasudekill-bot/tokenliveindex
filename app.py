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
app.config['CACHE_DEFAULT_TIMEOUT'] = 300 # 5 menit
cache = Cache(app)

# Route untuk halaman utama
@app.route('/')
def home():
    return render_template('index.html')

# ==========================================
# 3. API KALKULATOR (HANYA COINGECKO)
# ==========================================
@app.route('/api/convert', methods=['GET'])
def convert():
    crypto_id = request.args.get('crypto')
    fiat_currency = request.args.get('fiat')
    amount = float(request.args.get('amount'))

    url = f"https://api.coingecko.com/api/v3/simple/price?ids={crypto_id}&vs_currencies={fiat_currency}"
    headers = {"x-cg-demo-api-key": COINGECKO_API_KEY}

    try:
        response = requests.get(url, headers=headers, timeout=5)
        data = response.json()
        
        if crypto_id in data and fiat_currency in data[crypto_id]:
            price = data[crypto_id][fiat_currency]
            total = amount * price
            
            return jsonify({
                "status": "success",
                "price": price,
                "total": round(total, 2),
                "fiat": fiat_currency.upper()
            })
        else:
            return jsonify({"status": "error", "message": "Data tidak ditemukan"})
            
    except Exception:
        return jsonify({"status": "error", "message": "Gagal terhubung ke CoinGecko"})

# ==========================================
# 4. FUNGSI HELPER: KONVERSI MATA UANG (FRANKFURTER API)
# ==========================================
def get_exchange_rate(target_fiat):
    if target_fiat == 'usd':
        return 1.0 # Kalau USD, tidak perlu dikali apa-apa
    try:
        url = f"https://api.frankfurter.app/latest?amount=1&from=USD&to={target_fiat.upper()}"
        res = requests.get(url, timeout=3)
        if res.status_code == 200:
            return res.json()['rates'][target_fiat.upper()]
    except Exception:
        pass
    return None

# ==========================================
# 5. API GRAFIK (ROTASI 3 JALUR + KONVERSI DINAMIS)
# ==========================================
@cache.cached(timeout=300, query_string=True)
@app.route('/api/chart', methods=['GET'])
def get_chart_data():
    crypto_id = request.args.get('crypto', 'bitcoin')
    days = request.args.get('days', '7')
    target_fiat = request.args.get('fiat', 'usd').lower() # MINTA DATA FIAT DARI HTML
    
    base_data = None
    source_name = None

    # ---------------------------------------------------
    # JALUR 1: COINGECKO (BISA LANGSUNG HAPUS DALAM BENTUK APA AJA)
    # ---------------------------------------------------
    try:
        url_cg = f"https://api.coingecko.com/api/v3/coins/{crypto_id}/market_chart?vs_currency={target_fiat}&days={days}"
        headers_cg = {"x-cg-demo-api-key": COINGECKO_API_KEY}
        response = requests.get(url_cg, headers=headers_cg, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if 'prices' in data:
                base_data = [{"time": int(item[0] / 1000), "value": item[1]} for item in data['prices']]
                source_name = "coingecko"
    except Exception:
        pass 

    # ---------------------------------------------------
    # JALUR 2 & 3: FALLBACK (AMBIL DATA USD DARI COINCAP/BINANCE)
    # ---------------------------------------------------
    if not base_data:
        # COINCAP (USD)
        try:
            url_cc = f"https://api.coincap.io/v2/assets/{crypto_id}/history?interval=h1"
            response_cc = requests.get(url_cc, timeout=5)
            if response_cc.status_code == 200:
                data_cc = response_cc.json()
                if 'data' in data_cc and len(data_cc['data']) > 0:
                    base_data = [{"time": int(item["time"] / 1000), "value": float(item["priceUsd"])} for item in data_cc['data']]
                    source_name = "coincap"
        except Exception:
            pass 

        # BINANCE (USD) - Jika CoinCap gagal
        if not base_data:
            binance_symbol_map = {
                "bitcoin": "BTCUSDT", "ethereum": "ETHUSDT", "tether": "USDTUSDC", "binancecoin": "BNBUSDT",
                "solana": "SOLUSDT", "ripple": "XRPUSDT", "usd-coin": "USDCUSDT", "cardano": "ADAUSDT",
                "dogecoin": "DOGEUSDT", "tron": "TRXUSDT", "avalanche-2": "AVAXUSDT", "polkadot": "DOTUSDT",
                "chainlink": "LINKUSDT", "toncoin": "TONUSDT", "shiba-inu": "SHIBUSDT", "litecoin": "LTCUSDT",
                "uniswap": "UNIUSDT", "stellar": "XLMUSDT"
            }
            limit_map = {"7": 168, "30": 720, "90": 2160}
            
            try:
                symbol_bn = binance_symbol_map.get(crypto_id, "BTCUSDT")
                url_bn = f"https://api.binance.com/api/v3/klines?symbol={symbol_bn}&interval=1h&limit={limit_map.get(days, 168)}"
                response_bn = requests.get(url_bn, timeout=5)
                if response_bn.status_code == 200:
                    data_bn = response_bn.json()
                    if len(data_bn) > 0:
                        base_data = [{"time": int(item[0] / 1000), "value": float(item[4])} for item in data_bn]
                        source_name = "binance"
            except Exception:
                pass

    # ---------------------------------------------------
    # MAGIC TRICK: KALAU DATA DARI FALLBACK ADALAH USD, TAPI USER MAU IDR
    # KITA KALIKAN DATA TERSEBUT DENGAN KURS MATA UANG
    # ---------------------------------------------------
    if base_data and target_fiat != 'usd' and source_name != "coingecko":
        rate = get_exchange_rate(target_fiat)
        if rate:
            for item in base_data:
                item['value'] = item['value'] * rate
            source_name = f"{source_name} (Converted to {target_fiat.upper()})"

    # ---------------------------------------------------
    # FINISH: KIRIM KE FRONTEND
    # ---------------------------------------------------
    if base_data:
        return jsonify({"status": "success", "data": base_data, "source": source_name})
    else:
        return jsonify({"status": "error", "message": "Semua server data gagal."})

# Route halaman statis
@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

# ==========================================
# JALANKAN SERVER
# ==========================================
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
