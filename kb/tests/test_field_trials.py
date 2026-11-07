from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import openpyxl  # noqa: E402

import field_trials as F  # noqa: E402


class FieldTrialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "SC Field Data 2023.xlsx"
        wb = openpyxl.Workbook()
        notes = wb.active
        notes.title = "Notes"
        notes.append(["Planted 6/22/2023"])
        m = wb.create_sheet("Plot ID to Genotype")
        m.append(["Plot ID", "Genotype ID", "PI+rep unique ID", "Maturity", "Rep"])
        genos = [f"PI {1001 + i}" for i in range(12)]
        plot = 0
        for rep in (1, 2, 3):
            for g in genos:
                plot += 1
                m.append([plot, g, f"{g}_{rep}", "Early", f"Rep {rep}"])
        eos = wb.create_sheet("EOS plot meas.")
        eos.append(["Plot ID", "PI+rep unique ID", "Avg. Height", "% Lodged", "Lodging type (root-0, shoot-1)", "Notes"])
        heights = {g: 100 + 60 * i for i, g in enumerate(genos)}
        plot = 0
        for rep in (1, 2, 3):
            for g in genos:
                plot += 1
                h = heights[g] + rep
                if plot == 13:      # rep 2 of PI 1001 and PI 1002 swapped when entered
                    h = 162
                elif plot == 14:
                    h = 102
                eos.append([plot, f"{g}_{rep}", h, "NA", 1, "Mixed plot" if plot == 9 else None])
        anth = wb.create_sheet("Maturity")
        anth.append(["Plot ID", "Unique ID", "Days after planting"])
        anth.append([1, "PI 1001_1", "##"])
        anth.append([2, "PI 1002_1", 63])
        wb.save(path)
        self.res = F.parse_workbook(path)
        self.by = {p["plot"]: p for p in self.res["plots"]}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_map_site_year(self):
        ds = self.res["dataset"]
        self.assertEqual((ds["site"], ds["year"], ds["n_plots"]), ("South Carolina", 2023, 36))
        self.assertEqual(self.by["1"]["genotype"], "PI 1001")
        self.assertEqual(self.by["13"]["rep"], 2)

    def test_traits_and_flags(self):
        self.assertEqual(self.by["1"]["traits"]["plant_height"], 101)
        self.assertNotIn("lodging_pct", self.by["1"]["traits"])  # 'NA'
        self.assertEqual(self.by["1"]["text"]["lodging_type"], "shoot")
        self.assertNotIn("days_to_anthesis", self.by["1"]["traits"])  # '##'
        self.assertEqual(self.by["2"]["traits"]["days_to_anthesis"], 63)
        self.assertIn("mixed plot or phenotype", self.by["9"]["flags"])

    def test_swapped_entries(self):
        swaps = [i for i in self.res["dataset"]["issues"] if i["kind"] == "swapped_entries"]
        self.assertTrue(swaps and "13 and 14" in swaps[0]["text"])


if __name__ == "__main__":
    unittest.main()
