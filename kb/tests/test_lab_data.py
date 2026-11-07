from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import openpyxl  # noqa: E402

import lab_data  # noqa: E402

HEADER = ["Sample ID", "Genotype", "crude protein (g/100g grain) dwb", "lys (g/100g grain) dwb",
          "lys (g/100g protein) dwb", "starch (g/100g grain) dwb", "amylose (g/100g grain) dwb",
          "crude fat (g/100g grain) dwb", "M-Distance actual Protein", "M-Distance actual Lysine",
          "M-Distance actual Starch", "Comment"]


def row(sid, geno, protein, lys, starch, amylose, fat, md, comment=None):
    return [sid, geno, protein, lys, lys / protein * 100 if isinstance(protein, float) and protein else None,
            starch, amylose, fat, md, md, md, comment]


class LabDataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "Test Panel 2025 NIR - R1 (1).xlsx"
        wb = openpyxl.Workbook()
        avgs = wb.active
        avgs.title = "avgs"
        # data rows above the header, as in a sheet sorted after the header was written
        avgs.append(row("9_84", "PI597950", 12.0, 0.30, 70.0, 15.0, 3.0, 3.0))
        avgs.append(HEADER)
        avgs.append(row("9_85", 251.0, 11.0, 0.28, 71.0, "avg", 3.2, 5.0))
        avgs.append(row("9_86", "Tx430", -50.0, 0.30, 70.0, 15.0, 3.0, 9.0))
        avgs.append(row("9_87", "BTx623", 10.0, 0.25, 72.0, 16.0, 3.1, 2.0))
        avgs.append(row("9_88", "SC 748", 14.0, 0.40, 68.0, 17.0, 3.3, 2.5))
        reps = wb.create_sheet("reps")
        reps.append(HEADER)
        for sid, p in [("2025 9-84", 11.9), ("2025 9-84", 12.1), ("2025 9-85", 11.0), ("2025 9-85", 11.0),
                       ("2025 9-86", -50.0), ("2025 9-86", -50.0)]:
            reps.append(row(sid, None, p, 0.30, 70.0, 15.0, 3.0, 3.0, "moldy, very glumey"))
        # scan 2 of 9_87 and 9_88 swapped
        reps.append(row("2025 9-87", None, 10.0, 0.25, 72.0, 16.0, 3.1, 2.0))
        reps.append(row("2025 9-87", None, 14.0, 0.40, 68.0, 17.0, 3.3, 2.5))
        reps.append(row("2025 9-88", None, 14.0, 0.40, 68.0, 17.0, 3.3, 2.5))
        reps.append(row("2025 9-88", None, 10.0, 0.25, 72.0, 16.0, 3.1, 2.0))
        cal = wb.create_sheet("Cal Models")
        cal.append(["NIR Calibration", "N", "LV", "R2", "RMSECV", "Slope", "Min", "Max"])
        cal.append(["Protein", 356, 12, 0.96, 0.49, 0.97, 7.4, 17.6])
        wb.save(path)
        self.res = lab_data.parse_workbook(path)
        self.by_id = {s["sample_id"]: s for s in self.res["samples"]}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_header_anywhere_and_title(self):
        ds = self.res["dataset"]
        self.assertEqual(ds["title"], "Test Panel 2025 NIR - R1")
        self.assertEqual(ds["n_samples"], 5)
        self.assertIn("9_84", self.by_id)  # the row above the header
        self.assertTrue(any(i["kind"] == "layout" for i in ds["issues"]))

    def test_scans_joined_across_id_formats_and_genotypes(self):
        s = self.by_id["9_84"]
        self.assertEqual(s["n_scans"], 2)
        self.assertEqual(s["genotype"], "PI 597950")
        self.assertEqual(self.by_id["9_85"]["genotype"], "251")
        self.assertEqual(s["condition"], {"mold": "present", "glumes": "heavy"})

    def test_quality_flags(self):
        self.assertEqual(self.by_id["9_84"]["qc"], "good")
        self.assertEqual(self.by_id["9_85"]["qc"], "usable")
        self.assertTrue(any("text 'avg'" in f for f in self.by_id["9_85"]["flags"]))
        self.assertEqual(self.by_id["9_86"]["qc"], "invalid")
        self.assertEqual(self.res["dataset"]["calibration"][0]["trait"], "protein")

    def test_swapped_scans_detected(self):
        swaps = [i for i in self.res["dataset"]["issues"] if i["kind"] == "swapped_scans"]
        self.assertEqual(len(swaps), 1)
        self.assertIn("9_87", swaps[0]["text"])
        self.assertIn("9_88", swaps[0]["text"])

    def test_keys_and_traits(self):
        self.assertEqual(lab_data.sample_key("2025  9-84 "), "9_84")
        self.assertEqual(lab_data.sample_key("VR24-089-8"), "VR24_089_8")
        self.assertEqual(lab_data.classify("amylose (g/100g starch) dwb"), ("trait", "amylose_starch"))
        self.assertEqual(lab_data.classify("MD_Lys"), ("md", "lys"))
        self.assertEqual(lab_data.trait_key("lysine"), "lysine_grain")
        self.assertTrue(lab_data.genotype_match("PI 597950", "PI 597950"))
        self.assertFalse(lab_data.genotype_match("PI 59795", "PI 597950"))


if __name__ == "__main__":
    unittest.main()
