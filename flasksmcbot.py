from flask import Flask, request, jsonify
from flask_cors import CORS
import MetaTrader5 as mt5
import pandas as pd
import time
import json
import requests
from datetime import datetime, timezone

app = Flask(__name__)
CORS(app)  # Enable CORS for frontend communication

# Trading Configurations
SYMBOLS = ["", "", "USDJPYm", "EURJPYm"]
TIMEFRAMES = [mt5.TIMEFRAME_M1, mt5.TIMEFRAME_M5, mt5.TIMEFRAME_M15]
RISK_PERCENTAGE = 10
STOP_LOSS_PIPS = 50
TARGET_PROFIT_DOLLARS = 1
SESSION_FILTER = [(5, 17)]

# Trading Bot Class
class MT5SMCBot:
    def __init__(self, login, password, server):
        self.login = login
        self.password = password
        self.server = server

    def connect(self):
        if not mt5.initialize():
            return {"status": "error", "message": "MT5 initialization failed"}

        authorized = mt5.login(self.login, password=self.password, server=self.server)
        if authorized:
            return {"status": "success", "message": "Connected to MT5 successfully"}
        else:
            return {"status": "error", "message": "MT5 login failed"}

    def get_market_data(self, symbol, timeframe, count=50):
        if not mt5.symbol_select(symbol, True):
            return {"status": "error", "message": f"Failed to select symbol: {symbol}"}
        
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
        if rates is None:
            return {"status": "error", "message": f"Failed to fetch market data for {symbol}"}
        
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        return df.to_dict(orient='records')

    def detect_smc_trend(self, df):
        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values

        if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
            return "BUY"
        elif highs[-1] < highs[-2] and lows[-1] < lows[-2]:
            return "SELL"
        if closes[-1] > highs[-2]:
            return "SELL"
        elif closes[-1] < lows[-2]:
            return "BUY"
        return None

    def place_trade(self, symbol, trade_type, lot_size):
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            return {"status": "error", "message": f"Failed to get tick for {symbol}"}

        price = tick.ask if trade_type == "BUY" else tick.bid
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot_size,
            "type": mt5.ORDER_TYPE_BUY if trade_type == "BUY" else mt5.ORDER_TYPE_SELL,
            "price": price,
            "deviation": 10,
            "magic": 123456,
            "comment": "SMC Trade",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return {"status": "error", "message": f"Trade failed: {result.comment}"}
        return {"status": "success", "message": f"Trade executed: {trade_type} {symbol} - Lot: {lot_size}"}

    def close_trades_by_profit_target(self):
        positions = mt5.positions_get()
        if positions is None or len(positions) == 0:
            return {"status": "info", "message": "No open positions to close"}

        closed_positions = []
        for pos in positions:
            # Check if the position's profit meets or exceeds the target profit in dollars
            if pos.profit >= TARGET_PROFIT_DOLLARS:
                symbol = pos.symbol
                volume = pos.volume
                # For BUY positions, we need to sell to close; for SELL positions, we need to buy.
                if pos.type == mt5.POSITION_TYPE_BUY:
                    tick = mt5.symbol_info_tick(symbol)
                    price = tick.bid
                    order_type = mt5.ORDER_TYPE_SELL
                else:
                    tick = mt5.symbol_info_tick(symbol)
                    price = tick.ask
                    order_type = mt5.ORDER_TYPE_BUY

                close_request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "volume": volume,
                    "type": order_type,
                    "position": pos.ticket,  # Specify the position ticket to close
                    "price": price,
                    "deviation": 10,
                    "magic": 123456,
                    "comment": "Closing trade on profit target",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mt5.ORDER_FILLING_IOC,
                }
                result = mt5.order_send(close_request)
                if result.retcode == mt5.TRADE_RETCODE_DONE:
                    closed_positions.append({"ticket": pos.ticket, "status": "closed", "profit": pos.profit})
                else:
                    closed_positions.append({"ticket": pos.ticket, "status": "failed", "comment": result.comment})

        if not closed_positions:
            return {"status": "info", "message": "No positions met the profit target for closure"}
        return {"status": "success", "closed_positions": closed_positions}

@app.route('/connect', methods=['POST'])
def connect_mt5():
    data = request.json
    bot = MT5SMCBot(data["account_number"], data["password"], data["server"])
    response = bot.connect()
    return jsonify(response)

@app.route('/market_data', methods=['POST'])
def market_data():
    data = request.json
    bot = MT5SMCBot(data["account_number"], data["password"], data["server"])
    response = bot.get_market_data(data["symbol"], data["timeframe"])
    return jsonify(response)

@app.route('/trade', methods=['POST'])
def trade():
    data = request.json
    bot = MT5SMCBot(data["account_number"], data["password"], data["server"])
    df = pd.DataFrame(bot.get_market_data(data["symbol"], data["timeframe"]))
    trade_signal = bot.detect_smc_trend(df)
    
    if trade_signal:
        response = bot.place_trade(data["symbol"], trade_signal, data["lot_size"])
    else:
        response = {"status": "error", "message": "No trade signal detected"}
    
    return jsonify(response)

@app.route('/close_trades', methods=['POST'])
def close_trades():
    data = request.json
    bot = MT5SMCBot(data["account_number"], data["password"], data["server"])
    response = bot.close_trades_by_profit_target()
    return jsonify(response)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
