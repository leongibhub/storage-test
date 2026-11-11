import pathlib, sys, tempfile, unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'scripts'))
from thermal import bandwidth_windows

class ThermalTests(unittest.TestCase):
    def test_weighted_windows_and_actual_drop(self):
        with tempfile.TemporaryDirectory() as d:
            p=pathlib.Path(d)/'bw.log'
            p.write_text('\n'.join(f'{i*1000}, {100 if i<=2 else 50}, 1, 1048576' for i in range(1,11)))
            r=bandwidth_windows([p],10)
            self.assertEqual(r['early_bw_bytes'],102400); self.assertEqual(r['late_bw_bytes'],51200)
            self.assertEqual(r['drop_fraction'],.5)
    def test_incomplete_or_invalid_samples_block(self):
        with tempfile.TemporaryDirectory() as d:
            p=pathlib.Path(d)/'bw.log'
            for content in ['1000, 100, 1, 4096','1000, nan, 1, 4096','1000, 100, 1, 4096\n500, 100, 1, 4096']:
                p.write_text(content)
                with self.assertRaises(ValueError): bandwidth_windows([p],10)
            with self.assertRaises(ValueError): bandwidth_windows([p,p],10)
