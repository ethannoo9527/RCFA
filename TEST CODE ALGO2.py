# This is a python example algorithm using REST API for the RIT ALGO2 Case
import signal
import requests
from time import sleep

# this class definition allows us to print error messages and stop the program when needed
class ApiException(Exception):
    pass

# this signal handler allows for a graceful shutdown when CTRL+C is pressed
def signal_handler(signum, frame):
    global shutdown
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    shutdown = True

# set your API key to authenticate to the RIT client
API_KEY = {'X-API-Key': 'EZ91106P'}
shutdown = False
# other settings for market making algo
SPREAD = 0.02
BUY_VOLUME = 500
SELL_VOLUME = 500

# this helper method returns the current 'tick' of the running case
def get_tick(session):
    resp = session.get('http://localhost:9999/v1/case')
    if resp.status_code == 401:
        raise ApiException('The API key provided in this Python code must match that in the RIT client (please refer to the API hyperlink in the client toolbar and/or the RIT – User Guide – REST API Documentation.pdf)')
    case = resp.json()
    return case['tick']

# this helper method returns the last close price for the given security, one tick ago
def ticker_close(session, ticker):
    payload = {'ticker': ticker, 'limit': 1}
    resp = session.get('http://localhost:9999/v1/securities/history', params=payload)
    if resp.status_code == 401:
        raise ApiException('The API key provided in this Python code must match that in the RIT client (please refer to the API hyperlink in the client toolbar and/or the RIT – User Guide – REST API Documentation.pdf)')
    ticker_history = resp.json()
    if ticker_history:
        return ticker_history[0]['close']
    else:
        raise ApiException('Response error. Unexpected JSON response.')

# this helper method submits a pair of limit orders to buy and sell VOLUME of each security, at the last price +/- SPREAD
def buy_sell(session, to_buy, to_sell, last):
    buy_payload = {'ticker': to_buy, 'type': 'LIMIT', 'quantity': BUY_VOLUME, 'action': 'BUY', 'price': last - SPREAD}
    sell_payload = {'ticker': to_sell, 'type': 'LIMIT', 'quantity': SELL_VOLUME, 'action': 'SELL', 'price': last + SPREAD}
    session.post('http://localhost:9999/v1/orders', params=buy_payload)
    session.post('http://localhost:9999/v1/orders', params=sell_payload)

# this helper method gets all the orders of a given type (OPEN/TRANSACTED/CANCELLED)
def get_orders(session, status):
    payload = {'status': status}
    resp = session.get('http://localhost:9999/v1/orders', params=payload)
    if resp.status_code == 401:
        raise ApiException('The API key provided in this Python code must match that in the RIT client (please refer to the API hyperlink in the client toolbar and/or the RIT – User Guide – REST API Documentation.pdf)')
    orders = resp.json()
    return orders

def get_top_of_book(session, ticker):
    # Common RIT endpoint; adjust if your API differs
    resp = session.get('http://localhost:9999/v1/securities/book', params={'ticker': ticker})
    if resp.status_code == 401:
        raise ApiException("Bad API key.")
    book = resp.json()

    # These key names sometimes differ across setups—adjust if needed
    bids = book.get('bids', [])
    asks = book.get('asks', [])

    best_bid = bids[0]['price'] if bids else None
    best_ask = asks[0]['price'] if asks else None
    bid_size = bids[0].get('quantity', 0) if bids else 0
    ask_size = asks[0].get('quantity', 0) if asks else 0
    return best_bid, best_ask, bid_size, ask_size

def select_ticker_to_trade(session, tickers, min_spread):
    best = None
    for ticker in tickers:
        best_bid, best_ask, bid_size, ask_size = get_top_of_book(session, ticker)
        if best_bid is None or best_ask is None or best_ask <= best_bid:
            continue
        spread = best_ask - best_bid
        if spread < min_spread:
            continue
        if (best is None) or (spread > best['spread']):
            best = {
                'ticker': ticker,
                'best_bid': best_bid,
                'best_ask': best_ask,
                'bid_size': bid_size,
                'ask_size': ask_size,
                'spread': spread,
            }
    return best

def get_position(session, ticker):
    # This endpoint/key may differ in your RIT client; adjust if needed.
    # Some versions: GET /v1/securities?ticker=ALGO returns a list of securities with 'position'.
    resp = session.get('http://localhost:9999/v1/securities', params={'ticker': ticker})
    if resp.status_code == 401:
        raise ApiException("Bad API key.")
    data = resp.json()

    # Often it's a list with one dict
    if isinstance(data, list) and len(data) > 0:
        return data[0].get('position', 0)
    # Sometimes it's a dict
    if isinstance(data, dict):
        return data.get('position', 0)

    return 0

def get_all_positions(session):
    resp = session.get('http://localhost:9999/v1/securities')
    if resp.status_code == 401:
        raise ApiException("Bad API key.")
    data = resp.json()
    positions = {}
    if isinstance(data, list):
        for item in data:
            ticker = item.get('ticker')
            if ticker is not None:
                positions[ticker] = item.get('position', 0)
    return positions

def cancel_order(session, order_id):
    # Some RIT APIs cancel by POST /v1/orders/cancel or DELETE /v1/orders/{id}
    # Adjust to match your client. This is a common pattern:
    session.post('http://localhost:9999/v1/commands/cancel', params={'id': order_id})

def place_limit(session, ticker, side, qty, price):
    payload = {'ticker': ticker, 'type': 'LIMIT', 'quantity': qty, 'action': side, 'price': price}
    resp = session.post('http://localhost:9999/v1/orders', params=payload)
    if resp.status_code == 401:
        raise ApiException("Bad API key.")
    data = resp.json()
    if isinstance(data, dict):
        return data.get('order_id') or data.get('id')
    return None

def compute_trade_volumes(
    market_spread,
    edge,
    liquidity,
    pos,
    max_long,
    max_short,
    base_volume,
    min_volume,
    max_volume,
    liquidity_target,
):
    # Scale size up with edge and headroom; tilt to reduce inventory risk.
    if market_spread <= 0:
        return min_volume, min_volume

    edge_ratio = min(1.0, max(0.0, edge / market_spread))
    edge_scale = 0.7 + 0.3 * edge_ratio
    liq_ratio = min(2.0, max(0.0, liquidity / float(liquidity_target))) if liquidity_target > 0 else 1.0
    liq_scale = 0.7 + 0.3 * liq_ratio

    long_headroom = max(0.0, max_long - pos)
    short_headroom = max(0.0, max_short + pos)
    long_scale = min(1.0, long_headroom / float(max_long)) if max_long > 0 else 0.0
    short_scale = min(1.0, short_headroom / float(max_short)) if max_short > 0 else 0.0

    base = base_volume * edge_scale * liq_scale
    buy_base = base * (0.5 + 0.5 * long_scale)
    sell_base = base * (0.5 + 0.5 * short_scale)

    if pos > 0 and max_long > 0:
        tilt = min(1.0, pos / float(max_long))
        buy_base *= max(0.2, 1.0 - tilt)
        sell_base *= 1.0 + 0.3 * tilt
    elif pos < 0 and max_short > 0:
        tilt = min(1.0, (-pos) / float(max_short))
        sell_base *= max(0.2, 1.0 - tilt)
        buy_base *= 1.0 + 0.3 * tilt

    buy_qty = max(min_volume, min(max_volume, int(buy_base)))
    sell_qty = max(min_volume, min(max_volume, int(sell_base)))
    return buy_qty, sell_qty

def main():
    global shutdown

    TICKERS = ['ALGO']

    # --- New risk/quote knobs ---
    MAX_LONG_EXPOSURE = 7500   # hard long inventory limit
    MAX_SHORT_EXPOSURE = 7500  # hard short inventory limit
    MAX_GROSS_POS = 25000      # sum of absolute positions across tickers
    MAX_NET_POS = 25000        # signed net position across tickers
    MAX_SINGLE_LONG = 12500    # per-ticker long cap
    MAX_SINGLE_SHORT = 12500   # per-ticker short cap
    BASE_EDGE = 0.01          # your minimum edge per side (like half-spread)
    REQUOTE_TOL = 0.01        # only replace if we're off by >= this much
    MIN_MARKET_SPREAD = 0.035 # don’t quote if market spread too tiny (edge gone)
    BUY_PREMIUM = 0.002       # small premium to improve buy execution
    SELL_DISCOUNT = 0.002     # small discount to improve sell execution
    PRICE_CUSHION = 0.001     # avoid crossing the spread
    ORDER_TTL_TICKS = 4       # how long to let orders rest before canceling
    BASE_VOLUME = 3500        # used as a ceiling for dynamic sizing
    MIN_TRADE_VOLUME = 1200
    MAX_TRADE_VOLUME = 6000
    LIQUIDITY_TARGET = 3000
    WARMUP_TICKS = 10         # lower sizing only
    RAMP_TICKS = 20           # linearly scale to full size
    WARMUP_VOLUME_SCALE = 0.25
    RAMP_START_SCALE = 0.4
    SLEEP_SEC = 0.25
    order_ticks = {}
    last_mode = None

    with requests.Session() as s:
        s.headers.update(API_KEY)

        tick = get_tick(s)
        start_tick = tick

        while (not shutdown) and (tick > 5) and (tick < 295):
            # 1) Decide which ticker to trade (no cycling)
            choice = select_ticker_to_trade(s, TICKERS, MIN_MARKET_SPREAD)
            if choice is None:
                sleep(SLEEP_SEC)
                tick = get_tick(s)
                continue

            TICKER = choice['ticker']
            best_bid = choice['best_bid']
            best_ask = choice['best_ask']
            bid_size = choice['bid_size']
            ask_size = choice['ask_size']
            market_spread = choice['spread']
            mid = (best_bid + best_ask) / 2.0

            # Optional: if the market spread is too tight, no room to make edge
            if market_spread < MIN_MARKET_SPREAD:
                sleep(SLEEP_SEC)
                tick = get_tick(s)
                continue

            # 2) Risk state
            pos = get_position(s, TICKER)
            positions = get_all_positions(s)
            gross_pos = sum(abs(p) for p in positions.values())
            net_pos = sum(positions.values())

            # If too long/short, stop quoting the side that increases risk
            allow_buy = (
                pos < MAX_LONG_EXPOSURE
                and pos < MAX_SINGLE_LONG
                and gross_pos < MAX_GROSS_POS
                and net_pos < MAX_NET_POS
            )
            allow_sell = (
                pos > -MAX_SHORT_EXPOSURE
                and pos > -MAX_SINGLE_SHORT
                and gross_pos < MAX_GROSS_POS
                and net_pos > -MAX_NET_POS
            )

            # 3) Compute dynamic quotes (simple version)
            # Edge: at least BASE_EDGE, but also respect a fraction of the market spread
            edge = max(BASE_EDGE, 0.25 * market_spread)

            # Inventory skew: push quotes to reduce inventory
            # If long (+pos): push both quotes DOWN to encourage selling / discourage buying
            # If short (-pos): push both quotes UP to encourage buying / discourage selling
            k = 0.00001  # tune this (bigger = more aggressive inventory control)
            skew = k * pos

            desired_bid = (mid - edge) - skew
            desired_ask = (mid + edge) - skew
            quote_bid = min(desired_bid + BUY_PREMIUM, best_ask - PRICE_CUSHION)
            quote_ask = max(desired_ask - SELL_DISCOUNT, best_bid + PRICE_CUSHION)

            if quote_bid >= quote_ask:
                sleep(SLEEP_SEC)
                tick = get_tick(s)
                continue

            elapsed = max(0, tick - start_tick)
            if elapsed < WARMUP_TICKS:
                phase_scale = WARMUP_VOLUME_SCALE
                mode = 'warmup'
            elif elapsed < (WARMUP_TICKS + RAMP_TICKS):
                ramp_progress = (elapsed - WARMUP_TICKS) / float(max(1, RAMP_TICKS))
                phase_scale = RAMP_START_SCALE + (1.0 - RAMP_START_SCALE) * ramp_progress
                mode = 'ramp'
            else:
                phase_scale = 1.0
                mode = 'normal'

            if mode != last_mode:
                print("Mode switch: {} at tick {}".format(mode, tick))
                last_mode = mode

            scaled_base_volume = max(1, int(BASE_VOLUME * phase_scale))
            scaled_min_volume = max(1, int(MIN_TRADE_VOLUME * phase_scale))
            scaled_max_volume = max(scaled_min_volume, int(MAX_TRADE_VOLUME * phase_scale))

            top_liquidity = min(bid_size, ask_size)
            buy_qty, sell_qty = compute_trade_volumes(
                market_spread,
                edge,
                top_liquidity,
                pos,
                MAX_LONG_EXPOSURE,
                MAX_SHORT_EXPOSURE,
                scaled_base_volume,
                scaled_min_volume,
                scaled_max_volume,
                LIQUIDITY_TARGET,
            )
            if (gross_pos + buy_qty) > MAX_GROSS_POS or (net_pos + buy_qty) > MAX_NET_POS:
                allow_buy = False
            if (gross_pos + sell_qty) > MAX_GROSS_POS or (net_pos - sell_qty) < -MAX_NET_POS:
                allow_sell = False

            # 4) Read your open orders and identify current bid/ask
            open_orders = get_orders(s, 'OPEN')
            open_order_ids = {o.get('order_id') for o in open_orders if o.get('order_id') is not None}
            for oid in list(order_ticks):
                if oid not in open_order_ids:
                    order_ticks.pop(oid, None)

            my_bid = None
            my_ask = None
            for o in open_orders:
                order_id = o.get('order_id')
                if order_id is None:
                    continue
                if o.get('ticker') != TICKER:
                    cancel_order(s, order_id)
                    order_ticks.pop(order_id, None)
                    continue
                if order_id not in order_ticks:
                    order_ticks[order_id] = tick
                if (tick - order_ticks[order_id]) >= ORDER_TTL_TICKS:
                    cancel_order(s, order_id)
                    order_ticks.pop(order_id, None)
                    continue
                if o.get('action') == 'BUY':
                    # if multiple, keep the best-priced one and cancel others later
                    if (my_bid is None) or (o.get('price', -1e9) > my_bid.get('price', -1e9)):
                        my_bid = o
                elif o.get('action') == 'SELL':
                    if (my_ask is None) or (o.get('price', 1e9) < my_ask.get('price', 1e9)):
                        my_ask = o

            # 5) Clean up extra orders (keep at most one BUY and one SELL)
            # Cancel any "extra" ALGO orders that are not the chosen my_bid/my_ask
            for o in open_orders:
                if o.get('ticker') != TICKER:
                    continue
                if my_bid and o.get('action') == 'BUY' and o.get('order_id') != my_bid.get('order_id'):
                    cancel_order(s, o.get('order_id'))
                    order_ticks.pop(o.get('order_id'), None)
                if my_ask and o.get('action') == 'SELL' and o.get('order_id') != my_ask.get('order_id'):
                    cancel_order(s, o.get('order_id'))
                    order_ticks.pop(o.get('order_id'), None)

            # 6) Requote logic: only replace if price is stale by REQUOTE_TOL
            # BUY side
            if allow_buy:
                if my_bid is None:
                    order_id = place_limit(s, TICKER, 'BUY', buy_qty, quote_bid)
                    if order_id is not None:
                        order_ticks[order_id] = tick
                else:
                    if abs(my_bid.get('price', 0) - quote_bid) >= REQUOTE_TOL:
                        cancel_order(s, my_bid.get('order_id'))
                        order_ticks.pop(my_bid.get('order_id'), None)
                        order_id = place_limit(s, TICKER, 'BUY', buy_qty, quote_bid)
                        if order_id is not None:
                            order_ticks[order_id] = tick
            else:
                # If buying not allowed, cancel existing buy
                if my_bid is not None:
                    cancel_order(s, my_bid.get('order_id'))
                    order_ticks.pop(my_bid.get('order_id'), None)

            # SELL side
            if allow_sell:
                if my_ask is None:
                    order_id = place_limit(s, TICKER, 'SELL', sell_qty, quote_ask)
                    if order_id is not None:
                        order_ticks[order_id] = tick
                else:
                    if abs(my_ask.get('price', 0) - quote_ask) >= REQUOTE_TOL:
                        cancel_order(s, my_ask.get('order_id'))
                        order_ticks.pop(my_ask.get('order_id'), None)
                        order_id = place_limit(s, TICKER, 'SELL', sell_qty, quote_ask)
                        if order_id is not None:
                            order_ticks[order_id] = tick
            else:
                if my_ask is not None:
                    cancel_order(s, my_ask.get('order_id'))
                    order_ticks.pop(my_ask.get('order_id'), None)

            sleep(SLEEP_SEC)
            tick = get_tick(s)

# this calls the main() method when you type 'python algo2.py' into the command prompt
if __name__ == '__main__':
    signal.signal(signal.SIGINT, signal_handler)
    main()
