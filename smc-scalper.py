import MetaTrader5 as mt5
import pandas as pd
import time
import json
import requests
from datetime import datetime, timezone

# Telegram Bot Setup
TELEGRAM_TOKEN = "AAFjd5zloE8neWr2tzhkirk3uE4GvlArWSE"
TELEGRAM_CHAT_ID = "7318697622"

# Trading Configurations
# Ensure these symbols exactly match what appears in Market Watch.
SYMBOLS = ["EURUSDm", "GBPUSDm", "USDJPYm", "USDCADm"]
TIMEFRAMES = [mt5.TIMEFRAME_M1, mt5.TIMEFRAME_M5, mt5.TIMEFRAME_M15]
RISK_PERCENTAGE = 20        # Risk per trade (1% of balance)
STOP_LOSS_PIPS = 200        # Stop loss in pips
TARGET_PROFIT_DOLLARS = 20  # Target profit in dollars
SESSION_FILTER = [(7, 17)] # Trade only during London & New York session (UTC)

class MT5SMCBot:
    def __init__(self, login, password, server):
        self.login = login
        self.password = password
        self.server = server

    def connect(self):
        if not mt5.initialize():
            print("MT5 initialization failed:", mt5.last_error())
            return False

        authorized = mt5.login(self.login, password=self.password, server=self.server)
        if authorized:
            print("Connected to MT5 successfully")
            return True
        else:
            print("MT5 login failed:", mt5.last_error())
            return False

    def get_market_data(self, symbol, timeframe, count=50):
        # Ensure the symbol is selected in MT5
        if not mt5.symbol_select(symbol, True):
            print(f"Failed to select symbol: {symbol} - Error: {mt5.last_error()}")
            return None

        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
        if rates is None:
            print(f"Failed to fetch market data for {symbol} - Error: {mt5.last_error()}")
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
        if account_info is None:
            print("Account info not available")
            return 0
        balance = account_info.balance
        risk_amount = (risk_percentage / 100) * balance

        # Base lot size calculation (this formula might need adjustments for each instrument)
        base_lot = risk_amount / (stop_loss_pips * 10)
        
        # Retrieve symbol trading parameters
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            print(f"Symbol info not available for {symbol}")
            return 0

        min_volume = symbol_info.volume_min
        volume_step = symbol_info.volume_step

        # Adjust base_lot to meet the minimum volume requirement
        if base_lot < min_volume:
            print(f"Calculated lot size {base_lot} is lower than minimum volume {min_volume}. Adjusting to minimum.")
            lot_size = min_volume
        else:
            lot_size = base_lot

        # Round the volume to a multiple of volume_step
        lot_size = round(lot_size / volume_step) * volume_step

        # Double-check it hasn't fallen below min_volume
        if lot_size < min_volume:
            lot_size = min_volume

        # Optionally, ensure lot_size doesn't exceed the maximum allowed volume
        if lot_size > symbol_info.volume_max:
            lot_size = symbol_info.volume_max

        return round(lot_size, 2)

    def calculate_target_pips(self, symbol, lot_size, target_profit_dollars):
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            print(f"Symbol info not available for {symbol}")
            return None

        tick_value = symbol_info.trade_tick_value  # Tick value per lot size
        if tick_value is None:
            print(f"Tick value not available for {symbol}")
            return None

        # Calculate target profit in pips
        target_profit_pips = target_profit_dollars / (lot_size * tick_value)
        return target_profit_pips

    def place_trade(self, symbol, trade_type, lot_size):
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            print(f"Failed to get tick for {symbol}")
            return

        symbol_info = mt5.symbol_info(symbol)
        if not symbol_info:
            print(f"Failed to get symbol info for {symbol}")
            return

        point = symbol_info.point
        # Safely check for 'stops_level' availability and set a default value if it's missing
        min_stop_distance = getattr(symbol_info, 'stops_level', 0) * point

        price = tick.ask if trade_type == "BUY" else tick.bid
        stop_loss = price - STOP_LOSS_PIPS * point if trade_type == "BUY" else price + STOP_LOSS_PIPS * point
        target_pips = self.calculate_target_pips(symbol, lot_size, TARGET_PROFIT_DOLLARS)

        if target_pips is None:
            return

        take_profit = price + target_pips * point if trade_type == "BUY" else price - target_pips * point

        # Ensure stop loss is not closer than the minimum stop distance
        if abs(price - stop_loss) < min_stop_distance:
            stop_loss = price - min_stop_distance if trade_type == "BUY" else price + min_stop_distance

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": lot_size,
            "type": mt5.ORDER_TYPE_BUY if trade_type == "BUY" else mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": stop_loss,
            "tp": take_profit,
            "deviation": 10,
            "magic": 123456,
            "comment": "SMC Trade",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"Trade failed for {symbol}: {result.comment}, Retcode: {result.retcode}")
        else:
            print(f"Trade executed: {trade_type} {symbol} - Lot: {lot_size}")
            self.send_telegram_alert(f"Trade executed: {trade_type} {symbol} - Lot: {lot_size}")

    def send_telegram_alert(self, message):
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
        try:
            requests.post(url, data=json.dumps(payload), headers={"Content-Type": "application/json"})
        except Exception as e:
            print("Failed to send Telegram alert:", e)

    def is_valid_session(self):
        now = datetime.now(timezone.utc)  # Use timezone-aware datetime
        for start_hour, end_hour in SESSION_FILTER:
            if start_hour <= now.hour <= end_hour:
                return True
        return False

    def run(self):
        if not self.connect():
            return

        while True:
            if not self.is_valid_session():
                print("Outside trading session. Waiting...")
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
                            print(f"{datetime.now()} - No trade signal for {symbol} on timeframe {timeframe}")

            time.sleep(60)

        mt5.shutdown()

if __name__ == "__main__":
    mt5_login = 208408905
    mt5_password = "Watoop@222"
    mt5_server = "Exness-MT5Trial9"

    bot = MT5SMCBot(mt5_login, mt5_password, mt5_server)
    bot.run()
