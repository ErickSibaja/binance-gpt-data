import unittest
from datetime import datetime,timezone,timedelta
from strategy_stc03 import bollinger,atr_adx,rsi_wilder,candidates
from strategy_stc01 import Bar

def bars(n,hours=1):
    t=datetime(2025,1,1,tzinfo=timezone.utc)
    return [Bar(t+i*timedelta(hours=hours),100,101,99,100,1000) for i in range(n)]

class Checks(unittest.TestCase):
    def test_bollinger_no_lookahead(self):
        b=bars(21);mean,low=bollinger(b)
        self.assertIsNone(mean[18]);self.assertEqual(mean[19],100)
        b[20]=Bar(b[20].t,200,201,199,200,100)
        mean2,_=bollinger(b)
        self.assertEqual(mean2[19],100)
    def test_adx_is_finite_flat_market(self):
        atr,adx=atr_adx(bars(80,4))
        self.assertEqual(adx[27],0)
        self.assertEqual(atr[79],2)
    def test_rsi_wilder(self):
        x=rsi_wilder([100.]*30)
        self.assertIsNone(x[13]);self.assertEqual(x[14],100)
    def test_no_signals_on_flat_market(self):
        self.assertEqual(list(candidates(bars(400,.25),bars(100),bars(40,4))),[])

if __name__=='__main__':unittest.main()
