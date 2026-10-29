import unittest
from datetime import datetime,timezone,timedelta
from strategy_stc01 import Bar,ema,rsi_wilder,indicators,aligned_signals,parse_utc
from backtest_stc01 import run

class Tests(unittest.TestCase):
    def test_ema_seed(self):
        self.assertEqual(ema([1,2,3,4],3),[None,None,2,3])
    def test_rsi(self):
        self.assertEqual(rsi_wilder(list(range(20)))[14],100)
    def test_no_future_timestamps(self):
        with self.assertRaises(ValueError): parse_utc('2026-10-29T00:00:00')
    def test_no_fabricated_signal_on_flat(self):
        start=datetime(2026,1,1,tzinfo=timezone.utc)
        def make(n,seconds):
            return [Bar(start+timedelta(seconds=i*seconds),100,100,100,100,1) for i in range(n)]
        a,b,c=make(1500,900),make(400,3600),make(250,14400)
        self.assertEqual(aligned_signals(a,b,c),[])
        r=run(a,b,c)
        self.assertEqual(r['trades_count'],0)
        self.assertIsNone(r['win_rate'])
    def test_spot_does_not_short(self):
        self.assertEqual(run([Bar(datetime(2026,1,1,tzinfo=timezone.utc),100,100,100,100,1)],[],[])['trades_count'],0)

if __name__=='__main__':unittest.main()
