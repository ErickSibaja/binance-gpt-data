import unittest
from datetime import datetime, timezone, timedelta
from strategy_stc04 import Candle, signals
from backtest_stc04 import run

BASE=datetime(2024,1,1,tzinfo=timezone.utc)
def bars(values):
    return [Candle(BASE+timedelta(days=i),p,p+1,p-1,p,100) for i,p in enumerate(values)]

class Tests(unittest.TestCase):
    def test_no_lookahead(self):
        x=bars([100+i*.1 for i in range(280)])
        sig=signals(x)
        self.assertEqual(sig[0]['execute_index'],200)
        self.assertEqual(sig[0]['signal_at_utc'],x[200].t.isoformat())
    def test_last_candle_not_used_for_signal(self):
        x=bars([100+i*.1 for i in range(280)])
        y=bars([100+i*.1 for i in range(279)]+[1])
        self.assertEqual(signals(x),signals(y))
    def test_daily_equity_and_full_cash_cap(self):
        x=bars([100+i*.25 for i in range(320)])
        out=run(x,BASE+timedelta(days=230),BASE+timedelta(days=310))
        self.assertTrue(out['transactions'])
        self.assertEqual(out['transactions'][0]['type'],'BUY')
        self.assertGreaterEqual(out['cash'],0)
        self.assertTrue(out['open_position_at_end'])
    def test_stay_out_in_downtrend(self):
        x=bars([500-i for i in range(320)])
        out=run(x,BASE+timedelta(days=230),BASE+timedelta(days=310))
        self.assertEqual(out['transactions'],[])
        self.assertEqual(out['ending_equity_marked'],10000)
    def test_60_days_momentum(self):
        x=bars([100+i for i in range(300)])
        sig=signals(x)
        self.assertAlmostEqual(sig[0]['momentum60'],x[199].c/x[139].c-1)

if __name__=='__main__':unittest.main()
