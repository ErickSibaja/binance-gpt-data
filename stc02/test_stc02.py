import unittest
from datetime import datetime,timezone,timedelta
from strategy_stc02 import ema,atr_wilder,signals
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'stc01'))
from strategy_stc01 import Bar

class Tests(unittest.TestCase):
 def test_ema_seed(self):
  self.assertEqual(ema([1,2,3,4],3),[None,None,2.,3.])
 def test_atr(self):
  t=datetime(2025,1,1,tzinfo=timezone.utc)
  bars=[Bar(t+timedelta(hours=i),100,102,99,101,100) for i in range(20)]
  self.assertAlmostEqual(atr_wilder(bars)[13],3.)
 def test_no_early_signals(self):
  t=datetime(2025,1,1,tzinfo=timezone.utc)
  b15=[Bar(t+timedelta(minutes=15*i),100,101,99,100,100) for i in range(400)]
  b1=[Bar(t+timedelta(hours=i),100,101,99,100,100) for i in range(100)]
  b4=[Bar(t+timedelta(hours=4*i),100,101,99,100,100) for i in range(25)]
  self.assertEqual(list(signals(b15,b1,b4)),[])
if __name__=='__main__':unittest.main()
