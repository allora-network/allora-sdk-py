import psycopg
import math
import logging
from datetime import datetime, timedelta
from typing import TypeVar, Any, Callable, Awaitable, List
from allora_sdk import RunContext, get_block_time

logger = logging.getLogger(__name__)
GT_LOG_PREFIX = '📐 '

async def querydb_ohlc(q: str, **params):
    conn = await psycopg.AsyncConnection.connect(
        dbname="upshot_client_prod",
        user="upshot_client_prod",
        host="minimalist-api-cluster.cluster-ro-ctxlte36ht3l.us-east-1.rds.amazonaws.com",
        port="5432"
    )

    async with conn.cursor() as cur:
        await cur.execute(q, params)
        return await cur.fetchall()

async def get_price(ticker: str, gt_time: datetime) -> float:
    # current price = close price of previous candle
    candle_width = timedelta(minutes=1)

    query = """
        select date, close::float
        from allora_ohlc_timeseries_history
        where ticker = %(ticker)s and date < %(date)s
        order by date
        desc limit 1
    """

    query_result = await querydb_ohlc(query, ticker = ticker, date = gt_time - candle_width)

    if len(query_result) < 1:
        raise Error(f'Could not find any price data for {ticker} before {gt_time - candle_width}')

    (time, price) = query_result[0]

    time_error = (gt_time - candle_width - time).total_seconds()

    logger.info(f'{GT_LOG_PREFIX}Price for {ticker}: wanted price at {gt_time}, got close price at {time} (error {time_error}s), value {price}')

    return float(price)

async def get_logreturn(ticker: str, start: datetime, end: datetime) -> float:
    # current price = close price of previous candle
    candle_width = timedelta(minutes=1)

    query = """(
            select date, close::float
            from allora_ohlc_timeseries_history
            where ticker = %(ticker)s and date <= %(date1)s
            order by date
            desc limit 1
        ) union (
            select date, close::float
            from allora_ohlc_timeseries_history
            where ticker = %(ticker)s and date <= %(date2)s
            order by date
            desc limit 1
        )
    """
    query_result = await querydb_ohlc(query, ticker = ticker, date1 = start - candle_width, date2 = end - candle_width)

    if len(query_result) < 2:
        raise Error(f'Could not find price values for log return {ticker} at times {start} and/or {end}')

    (time_base, price_base) = query_result[0]
    (time_target, price_target) = query_result[1]

    time_error_base = (start - candle_width - time_base).total_seconds()
    time_error_target = (end - candle_width - time_target).total_seconds()

    if math.isnan(price_target) or math.isnan(price_base) or price_target <= 0 or price_base <= 0:
        raise Error(f'Invalid price values for {ticker} at {start} and {end}: {price_base} and {price_target}')

    logreturn = math.log(price_target / price_base)

    logger.info(f'{GT_LOG_PREFIX}Log return for {ticker}: wanted prices at {start} and {end}, got close prices at {time_base} and {time_target} (candle width {candle_width}, errors {time_error_base:.1f}s, {time_error_target:.1f}s), values {price_base} and {price_target}, log return {logreturn}')

    return logreturn

async def get_logreturn_stddev(ticker: str, end: datetime, timeframe_seconds: int, window: int) -> float:
    query = """
        with samples as (
            select
                date,
                close::float as close,
                row_number() over (
                    partition by (extract(epoch from date)::int - %(offset)s) / %(timeframe)s
                    order by extract(epoch from date)::int desc
                ) as row
            from allora_ohlc_timeseries_history
            where
                ticker = %(ticker)s and
                date >= %(start)s and
                date < %(end)s
        ), log_return as (
            select
                ln(close / lag(close) over (order by date asc)) as log_return
            from samples
            where
            row = 1
        ) select
            count(*) as n,
            sum(log_return) as sx,
            sum(log_return*log_return) as sx2
        from log_return
        where
        log_return is not null
    """
    offset = int(end.timestamp()) % timeframe_seconds
    start = end - timedelta(seconds=(window+1)*timeframe_seconds)

    max_lookback = timedelta(days=365)
    if end - start > max_lookback:
        start = end - max_lookback

    query_result = await querydb_ohlc(query, ticker = ticker, start = start, end = end, offset = offset, timeframe = timeframe_seconds)

    (n, sx, sx2) = query_result[0]

    if n < 1:
        raise Error(f'Could not find price values for {ticker} volatility between {start} and {end}')

    std = math.sqrt(sx2/n - (sx/n)**2)

    logger.info(f'{GT_LOG_PREFIX}Log return standard deviation for {ticker}: between {start} and {end} (timeframe {timeframe_seconds}s), got {n}/{window} samples, stddev {std}')

    return std

async def get_price_volatility(ticker: str, start: datetime, end: datetime) -> float:
    query = """
        select count(*), sum(close)::float, sum(close*close)::float
        from allora_ohlc_timeseries_history
        where ticker = %(ticker)s and date >= %(start)s and date < %(end)s
    """
    query_result = await querydb_ohlc(query, lag = 1, ticker = ticker, start = start, end = end)

    (n, sx, sx2) = query_result[0]
    volatility = math.sqrt(sx2/n - (sx/n)**2)

    if n < 1:
        raise Error(f'Could not find price values for {ticker} volatility between {start} and {end}')

    logger.info(f'{GT_LOG_PREFIX}Price volatility for {ticker}: between {start} and {end}, got {n} samples, raw volatility {volatility}')

    return volatility

def make_gt_function(mode: str, ticker: str, offset: timedelta) -> Callable[[RunContext], Awaitable[any]]:
    async def gt_func(context: RunContext) -> any:
        nonce_time = await get_block_time(context.client, context.nonce)

        if mode == 'price':
            return await get_price(ticker, nonce_time + offset)
        elif mode == 'logreturn':
            return await get_logreturn(ticker, nonce_time, nonce_time + offset)
        elif mode == 'logreturn+stddev':
            logreturn = await get_logreturn(ticker, nonce_time, nonce_time + offset)
            stddev = await get_logreturn_stddev(ticker, nonce_time + offset, int(offset.total_seconds()), 100)
            return (logreturn, stddev)
        elif mode == 'volatility':
            return await get_price_volatility(ticker, nonce_time, nonce_time + offset)
        else:
            raise NotImplementedError(f'Ground truth mode "{mode}" is not supported')

    return gt_func
