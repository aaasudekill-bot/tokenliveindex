import os
import sys
import datetime
import time
import atexit
from flask import Flask, request, jsonify
import requests
from flask_caching import Cache
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)

# ==========================================
# 1. KONFIGURASI UTAMA
# ==========================================
COINGECKO_API_KEY = "CG-5q1PNRDHjFvyNPyx4RdZjJkb"

CORS(app)

app.config['CACHE_TYPE'] = 'SimpleCache'
app.config['CACHE_DEFAULT_TIMEOUT'] = 300
cache = Cache(app)

# Fix untuk psycopg v3 & URL Database Render/Supabase
database_url = os.environ.get('DATABASE_URL', 'sqlite:///predictions.db')

if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql+psycopg://", 1)
elif database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

if database_url.startswith("postgresql+psycopg://"):
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'connect_args': {
            'prepare_threshold': 0
        }
    }

db = SQLAlchemy(app)

# ==========================================
# 2. MODEL DATABASE
# ==========================================
class Prediction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    crypto_id = db.Column(db.String(50), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    price_at_pred = db.Column(db.Float, nullable=False)
    prediction = db.Column(db.String(10), nullable=False)
    price_at_result = db.Column(db.Float, nullable=True)
    status = db.Column(db.String(10), default='PENDING')

class ChartCache(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    crypto_id = db.Column(db.String(50), unique=True, nullable=False)
    chart_data = db.Column(db.JSON, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

# ==========================================
# 3. HELPER & MAPPING
# ==========================================
binance_symbol_map = { 
    "bitcoin": "BTCUSDT", "ethereum": "ETHUSDT", 
    "binancecoin": "BNBUSDT", "solana": "SOLUSDT", "ripple": "XRPUSDT", 
    "cardano": "ADAUSDT", "dogecoin": "DOGEUSDT", 
    "tron": "TRXUSDT", "avalanche-2": "AVAXUSDT", "polkadot": "DOTUSDT", 
    "chainlink": "LINKUSDT", "toncoin": "TONUSDT", "shiba-inu": "SHIBUSDT", 
    "litecoin": "LTCUSDT", "uniswap": "UNIUSDT", "stellar": "XLMUSDT",
    "hyperliquid": "HYPEUSDT" # DITAMBAHKAN: Hyperliquid
}

def get_exchange_rate(target_fiat):
    if target_fiat == 'usd': return 1.0
    try:
        url_er = f"https://open.er-api.com/v6/latest/USD"
        res_er = requests.get(url_er, timeout=3)
        if res_er.status_code == 200:
            data_er = res_er.json()
            if 'rates' in data_er and target_fiat.upper() in data_er['rates']:
                return data_er['rates'][target_fiat.upper()]
    except Exception: pass
    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids=usd&vs_currencies={target_fiat}"
        headers = {"x-cg-demo-api-key": COINGECKO_API_KEY}
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if 'usd' in data and target_fiat in data['usd']: return data['usd'][target_fiat]
    except Exception: pass
    return None

# ==========================================
# 4. HALAMAN UTAMA & API ROUTES
# ==========================================

@app.route('/')
def index():
    return "API LIVES!"

@app.route('/api/convert', methods=['GET'])
def convert():
    crypto_id = request.args.get('crypto_id')
    fiat_currency = request.args.get('fiat_currency', 'usd')
    amount = request.args.get('amount', '1')
    try: amount = float(amount)
    except ValueError: return jsonify({"status": "error", "message": "Invalid amount"})
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={crypto_id}&vs_currencies={fiat_currency}"
    headers = {"x-cg-demo-api-key": COINGECKO_API_KEY}
    try:
        response = requests.get(url, headers=headers, timeout=5)
        data = response.json()
        if crypto_id in data and fiat_currency in data[crypto_id]:
            price = data[crypto_id][fiat_currency]
            return jsonify({"status": "success", "price": price, "total": round(float(amount) * price, 2), "fiat": fiat_currency.upper()})
        else: return jsonify({"status": "error", "message": "Asset or currency not recognized"})
    except Exception: return jsonify({"status": "error", "message": "Failed to connect to pricing server"})

@cache.cached(timeout=300, query_string=True)
@app.route('/api/chart', methods=['GET'])
def get_chart_data():
    crypto_id = request.args.get('crypto_id', 'bitcoin')
    days = request.args.get('days', '7')
    fiat_currency = request.args.get('fiat_currency', 'usd')
    base_data = None; store_name = ""

    if crypto_id in binance_symbol_map:
        limit_map = {"1": 24, "7": 168, "14": 336, "30": 720, "90": 2160}
        try:
            symbol_bn = binance_symbol_map[crypto_id]
            limit = limit_map.get(days, 168)
            url_bn = f"https://api.binance.com/api/v3/klines?symbol={symbol_bn}&interval=1h&limit={limit}"
            response_bn = requests.get(url_bn, timeout=3)
            if response_bn.status_code == 200:
                data_bn = response_bn.json()
                if len(data_bn) > 2: 
                    base_data = [{"time": int(item[0] / 1000), "value": float(item[4])} for item in data_bn]
                    store_name = "binance"
        except Exception: pass

    if not base_data:
        try:
            url = f"https://api.coingecko.com/api/v3/coins/{crypto_id}/market_chart?vs_currency={fiat_currency}&days={days}"
            headers = {"x-cg-demo-api-key": COINGECKO_API_KEY}
            response = requests.get(url, headers=headers, timeout=5)
            if response.status_code == 200:
                data = response.json()
                if 'prices' in data: base_data = [{"time": int(item[0] / 1000), "value": item[1]} for item in data['prices']]; store_name = "coingecko"
        except Exception: pass

    if not base_data:
        try:
            url_cc = f"https://api.coincap.io/v2/assets/{crypto_id}/history?interval=h1"
            response_cc = requests.get(url_cc, timeout=3)
            if response_cc.status_code == 200:
                data_cc = response_cc.json()
                if 'data' in data_cc and len(data_cc['data']) > 0: base_data = [{"time": int(item['time'] / 1000), "value": float(item['priceUsd'])} for item in data_cc['data']]; store_name = "coincap"
        except Exception: pass

    if base_data and fiat_currency != 'usd' and store_name != "coingecko":
        rate = get_exchange_rate(fiat_currency)
        if rate:
            for item in base_data: item['value'] = item['value'] * rate
            store_name = f"{store_name} (Converted to {fiat_currency.upper()})"

    if base_data: return jsonify({"status": "success", "data": base_data, "source": store_name})
    else: return jsonify({"status": "error", "message": "All data sources failed to load"})

@app.route('/api/sentimen')
def get_sentimen():
    url = "https://api.alternative.me/fng/?limit=30"
    try:
        response = requests.get(url, timeout=8); data = response.json()
        if 'data' in data: return jsonify({"status": "success", "current": data['data'][0], "history": data['data']})
        else: return jsonify({"status": "error", "message": "Sentiment data unavailable"})
    except Exception: return jsonify({"status": "error", "message": "Connection timeout"})

@app.route('/api/pivot')
def get_pivot_points():
    crypto_id = request.args.get('crypto_id', 'bitcoin')
    symbol = binance_symbol_map.get(crypto_id, "BTCUSDT")
    url_bn = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1d&limit=1"
    try:
        response_bn = requests.get(url_bn, timeout=8)
        if response_bn.status_code == 200:
            data_bn = response_bn.json()[0]; high = float(data_bn[2]); low = float(data_bn[3]); close = float(data_bn[4])
            pp = (high + low + close) / 3; r1 = (2 * pp) - low; s1 = (2 * pp) - high; r2 = pp + (high - low); s2 = pp - (high - low)
            return jsonify({"status": "success", "data": {"pp": round(pp, 2), "r1": round(r1, 2), "r2": round(r2, 2), "s1": round(s1, 2), "s2": round(s2, 2)}})
    except Exception: pass
    return jsonify({"status": "error", "message": "Failed to calculate pivot points"})

# ==========================================
# 5. AI PREDICTION HELPERS
# ==========================================
# DITAMBAHKAN: hyperliquid
SUPPORTED_COINS = [ "bitcoin", "ethereum", "binancecoin", "solana", "ripple", "cardano", "dogecoin", "tron", "avalanche-2", "polkadot", "chainlink", "toncoin", "shiba-inu", "litecoin", "uniswap", "stellar", "hyperliquid" ]

def get_live_price(crypto_id):
    try:
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={crypto_id}&vs_currencies=usd"
        headers = {"x-cg-demo-api-key": COINGECKO_API_KEY}
        res = requests.get(url, headers=headers, timeout=5).json()
        if crypto_id in res and 'usd' in res[crypto_id]: return res[crypto_id]['usd']
    except: pass
    try:
        url_cc = f"https://api.coincap.io/v2/assets/{crypto_id}"
        res_cc = requests.get(url_cc, timeout=5).json()
        if 'data' in res_cc and 'priceUsd' in res_cc['data']: return float(res_cc['data']['priceUsd'])
    except: pass
    try:
        symbol = binance_symbol_map.get(crypto_id, f"{crypto_id.upper()}USDT")
        url_bn = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
        res_bn = requests.get(url_bn, timeout=5).json()
        if 'price' in res_bn: return float(res_bn['price'])
    except: pass
    return None

def get_24h_history(crypto_id):
    try:
        url = f"https://api.coingecko.com/api/v3/coins/{crypto_id}/market_chart?vs_currency=usd&days=1"
        headers = {"x-cg-demo-api-key": COINGECKO_API_KEY}
        res = requests.get(url, headers=headers, timeout=5).json()
        if 'prices' in res and len(res['prices']) > 4: return res['prices']
    except: pass
    try:
        url_cc = f"https://api.coincap.io/v2/assets/{crypto_id}/history?interval=h1"
        res_cc = requests.get(url_cc, timeout=5).json()
        if 'data' in res_cc and len(res_cc['data']) > 4:
            return [[int(item['time']), float(item['priceUsd'])] for item in res_cc['data']]
    except: pass
    try:
        symbol = binance_symbol_map.get(crypto_id, f"{crypto_id.upper()}USDT")
        url_bn = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1h&limit=24"
        res_bn = requests.get(url_bn, timeout=5).json()
        if len(res_bn) > 4:
            return [[int(item[0]), float(item[4])] for item in res_bn]
    except: pass
    return None

def ai_predict_job():
    with app.app_context():
        now = datetime.datetime.utcnow()
        one_hour_ago = now - datetime.timedelta(hours=1)
        pending_preds = Prediction.query.filter(Prediction.timestamp <= one_hour_ago, Prediction.status == 'PENDING').all()
        for pred in pending_preds:
            current_price = get_live_price(pred.crypto_id)
            if current_price:
                pred.price_at_result = current_price
                if pred.prediction == 'UP' and current_price > pred.price_at_pred: pred.status = 'WIN'
                elif pred.prediction == 'DOWN' and current_price < pred.price_at_pred: pred.status = 'WIN'
                else: pred.status = 'LOSE'
                db.session.commit()
        expired_time = now - datetime.timedelta(hours=24)
        Prediction.query.filter(Prediction.timestamp < expired_time).delete()
        db.session.commit()
        for coin in SUPPORTED_COINS:
            history = get_24h_history(coin)
            if history:
                current_price = history[-1][1]
                price_4h_ago = history[-4][1] if len(history) > 4 else history[0][1]
                pred_direction = 'UP' if current_price > price_4h_ago else 'DOWN'
                new_pred = Prediction(crypto_id=coin, price_at_pred=current_price, prediction=pred_direction)
                db.session.add(new_pred)
        db.session.commit()
        print(f"[{now}] AI Prediction Job Completed!")

# ==========================================
# 6. CHART CACHE SCHEDULER
# ==========================================
def update_chart_cache():
    with app.app_context():
        print("[Chart Cache] Mulai update data chart...")
        try:
            time_limit = datetime.datetime.utcnow() - datetime.timedelta(hours=24)
            stale_data = ChartCache.query.filter(ChartCache.updated_at < time_limit).all()
            if stale_data:
                for stale in stale_data: db.session.delete(stale)
                db.session.commit()
        except Exception as e: pass

        for coin in SUPPORTED_COINS:
            base_data = None
            try:
                url = f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart?vs_currency=usd&days=1"
                headers = {"x-cg-demo-api-key": COINGECKO_API_KEY}
                res = requests.get(url, headers=headers, timeout=5)
                if res.status_code == 200:
                    data = res.json()
                    if 'prices' in data and len(data['prices']) > 2:
                        base_data = [{"time": int(item[0] / 1000), "value": item[1]} for item in data['prices']]
            except Exception: pass
            
            if not base_data:
                try:
                    url_cc = f"https://api.coincap.io/v2/assets/{coin}/history?interval=h1"
                    res_cc = requests.get(url_cc, timeout=3)
                    if res_cc.status_code == 200:
                        data_cc = res_cc.json()
                        if 'data' in data_cc and len(data_cc['data']) > 2:
                            base_data = [{"time": int(item['time'] / 1000), "value": float(item['priceUsd'])} for item in data_cc['data']]
                except Exception: pass

            if base_data:
                now = datetime.datetime.utcnow()
                twenty_four_hours_ago_ts = int((now - datetime.timedelta(hours=24)).timestamp())
                filtered_data = [item for item in base_data if item['time'] >= twenty_four_hours_ago_ts]
                if filtered_data and len(filtered_data) > 2:
                    existing = ChartCache.query.filter_by(crypto_id=coin).first()
                    if existing:
                        existing.chart_data = filtered_data
                        existing.updated_at = datetime.datetime.utcnow()
                    else:
                        new_cache = ChartCache(crypto_id=coin, chart_data=filtered_data)
                        db.session.add(new_cache)
                    db.session.commit()
            time.sleep(1.5)
        print("[Chart Cache] Selesai update data chart!")

@app.route('/api/cached-chart')
def get_cached_chart():
    crypto_id = request.args.get('crypto_id', 'bitcoin')
    cache_entry = ChartCache.query.filter_by(crypto_id=crypto_id).first()
    if cache_entry and cache_entry.chart_data:
        return jsonify({"status": "success", "data": cache_entry.chart_data})
    else:
        return jsonify({"status": "error", "message": "Chart data not cached yet"}), 404

# ==========================================
# 7. REAL-TIME BINANCE 2 DETIK
# ==========================================
def update_binance_prices():
    try:
        url_bn = "https://api.binance.com/api/v3/ticker/price"
        response = requests.get(url_bn, timeout=3)
        if response.status_code == 200:
            data = response.json()
            price_dict = {item['symbol']: float(item['price']) for item in data}
            cache.set('binance_live_prices', price_dict, timeout=10)
    except Exception as e: pass

@app.route('/api/live-prices')
def get_live_prices():
    prices = cache.get('binance_live_prices')
    if prices is None:
        update_binance_prices()
        prices = cache.get('binance_live_prices')
    if prices is None: return jsonify({"status": "error", "message": "Gagal mengambil data dari Binance"}), 500
    return jsonify({"status": "success", "data": prices})

# ==========================================
# 8. COINGECKO TOP 250 KOIN (5 MENIT)
# ==========================================
def update_coingecko_top250():
    try:
        url_cg = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page=1&sparkline=false"
        headers = {"x-cg-demo-api-key": COINGECKO_API_KEY}
        response = requests.get(url_cg, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            price_dict = {}
            for coin in data:
                price_dict[coin['id']] = {"price": coin['current_price'], "symbol": coin['symbol'].upper(), "image": coin['image'], "change_24h": coin['price_change_percentage_24h']}
            cache.set('coingecko_top_prices', price_dict, timeout=600)
    except Exception as e: pass

@app.route('/api/top-coins')
def get_top_coins():
    return jsonify({"status": "success", "binance_realtime": cache.get('binance_live_prices') or {}, "coingecko_top250": cache.get('coingecko_top_prices') or {}})

# ==========================================
# 9. SCHEDULLER & INIT
# ==========================================
try:
    with app.app_context():
        db.create_all()
except Exception as e:
    print(f"FATAL ERROR INITIALIZING DATABASE: {e}", file=sys.stderr)

scheduler = BackgroundScheduler()
scheduler.add_job(func=ai_predict_job, trigger="cron", minute="5")
scheduler.add_job(func=update_binance_prices, trigger="interval", seconds=2)
scheduler.add_job(func=update_coingecko_top250, trigger="interval", minutes=5)
scheduler.add_job(func=update_chart_cache, trigger="interval", minutes=5)
scheduler.add_job(func=update_binance_prices, trigger="date")
scheduler.add_job(func=update_coingecko_top250, trigger="date")
scheduler.add_job(func=update_chart_cache, trigger="date")
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

@app.route('/api/ai-predictions')
def get_ai_predictions():
    predictions = Prediction.query.order_by(Prediction.timestamp.desc()).all()
    result = []
    for p in predictions:
        result.append({ "crypto_id": p.crypto_id, "timestamp": p.timestamp.isoformat(), "price_at_pred": p.price_at_pred, "prediction": p.prediction, "price_at_result": p.price_at_result, "status": p.status })
    return jsonify({"status": "success", "data": result})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
