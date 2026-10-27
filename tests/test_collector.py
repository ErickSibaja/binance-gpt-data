import unittest
from collector import ema, rsi_wilder, validate

class IndicatorsTest(unittest.TestCase):
    def test_ema_constant(self):
        self.assertAlmostEqual(ema([100.0]*300,200),100.0)
    def test_ema_increase(self):
        self.assertGreater(ema(list(range(1,301)),9),ema(list(range(1,301)),21))
    def test_rsi_flat(self):
        self.assertEqual(rsi_wilder([100.0]*300),50.0)
    def test_rsi_up(self):
        self.assertEqual(rsi_wilder(list(map(float,range(1,301)))),100.0)
    def test_not_enough(self):
        with self.assertRaises(ValueError): ema([1.0],9)
    def test_missing_bars(self):
        raw=[]
        for i in range(301):
            t=i*900_000+(900_000 if i>=150 else 0)
            raw.append([t,'1','1','1','1','1',t+899_999,'1','0','0','0','0'])
        with self.assertRaisesRegex(ValueError,'consecutivos'):
            validate(raw,900_000,500_000_000)

if __name__=='__main__': unittest.main()
