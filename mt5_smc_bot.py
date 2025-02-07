import MetaTrader5 as mt5
import pandas as pd
import time
import json
import requests
from datetime import datetime

# Telegram Bot Setup
TELEGRAM_TOKEN = "your_telegram_bot_token"
TELEGRAM_CHAT_ID = "your_chat_id"

# Trading Configurations
SYMBOLS = ["EURUSD", "XAUUSD", "USDJPY", "NAS100"]
TIMEFRAMES = [mt5.TIMEFRAME_M1, mt5.TIMEFRAME_M5, mt5.TIMEFRAME_M15]
RISK_PERCENTAGE = 1  # Risk per trade (1% of balance)
STOP_LOSS_PIPS = 50  # Stop loss in pips
TAKE_PROFIT_PIPS = 100  # Take profit in pips
SESSION_FILTER = [(7, 17)]  # Trade only during London & New York (7 AM - 5 PM UTC)

class MT5SMCBot:
    def __init__(self, login, password, server):
        self.login = login
        self.password = password
        self.server = server

    def connect(self):
        if not mt5.initialize():
            print("MT5 initialization failed")
            return False

        authorized = mt5.login(self.login, password=self.password, server=self.server)
        if authorized:
            print("Connected to MT5 successfully")
            return True
        else:
            print("MT5 login failed:", mt5.last_error())
            return False

    def get_market_data(self, symbol, timeframe, count=50):
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
        if rates is None:
            print(f"Failed to fetch market data for {symbol}")
            return None

        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        return df

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

    def calculate_lot_size(self, risk_percentage, stop_loss_pips, symbol):
        account_info = mt5.account_info()
        balance = account_info.balance
        risk_amount = (risk_percentage / 100) * balance
        lot_size = risk_amount / (stop_loss_pips * 10)
        return round(lot_size, 2)

    def place_trade(self, symbol, trade_type, lot_size):
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            print(f"Failed to get price for {symbol}")
            return

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot_size,
            "type": mt5.ORDER_TYPE_BUY if trade_type == "BUY" else mt5.ORDER_TYPE_SELL,
            "price": tick.ask if trade_type == "BUY" else tick.bid,
            "sl": tick.ask - STOP_LOSS_PIPS * 0.0001 if trade_type == "BUY" else tick.bid + STOP_LOSS_PIPS * 0.0001,
            "tp": tick.ask + TAKE_PROFIT_PIPS * 0.0001 if trade_type == "BUY" else tick.bid - TAKE_PROFIT_PIPS * 0.0001,
            "deviation": 10,
            "magic": 123456,
            "comment": "SMC Trade",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"Trade failed: {result.comment}")
        else:
            print(f"Trade executed: {trade_type} {symbol} - Lot: {lot_size}")
            self.send_telegram_alert(f"Trade executed: {trade_type} {symbol} - Lot: {lot_size}")

    def send_telegram_alert(self, message):
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
        requests.post(url, data=json.dumps(payload), headers={"Content-Type": "application/json"})

    def is_valid_session(self):
        now = datetime.utcnow()
        for start_hour, end_hour in SESSION_FILTER:
            if start_hour <= now.hour <= end_hour:
                return True
        return False

    def run(self):
        if not self.connect():
            return

        while True:
            if not self.is_valid_session():
                print("Outside of trading session. Waiting...")
                time.sleep(300)
                continue

            for symbol in SYMBOLS:
                for timeframe in TIMEFRAMES:
                    df = self.get_market_data(symbol, timeframe)
                    if df is not None:
                        decision = self.detect_smc_trend(df)
                        if decision:
                            lot_size = self.calculate_lot_size(RISK_PERCENTAGE, STOP_LOSS_PIPS, symbol)
                            self.place_trade(symbol, decision, lot_size)
                        else:
                            print(f"{datetime.now()} - No trade signal for {symbol} on {timeframe}")

            time.sleep(60)

        mt5.shutdown()

if __name__ == "__main__":
    mt5_login = 12345678
    mt5_password = "your_password"
    mt5_server = "Deriv-Server"

    bot = MT5SMCBot(mt5_login, mt5_password, mt5_server)
    bot.run()
