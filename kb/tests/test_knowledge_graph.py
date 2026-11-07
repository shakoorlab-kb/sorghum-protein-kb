from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from knowledge_graph import KnowledgeGraph  # noqa: E402


class KnowledgeGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.graph = KnowledgeGraph(Path(self.tmp.name) / "graph.sqlite3")
        self.paper = {
            "paper_id": "paper_a",
            "title": "Fermentation and sorghum digestibility",
            "doi": "10.1234/example.a",
            "year": 2025,
            "authors": ["A. Researcher"],
            "journal": "Sorghum Science",
            "keywords": ["food processing"],
            "abstract": (
                "Fermentation improves protein digestibility and reduces tannins in "
                "Sorghum bicolor grain."
            ),
            "key_claims": [
                "Disulfide bonds in kafirin contribute to low protein digestibility."
            ],
            "references": ["Prior work. doi:10.5555/cited.paper"],
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_index_search_and_relationship_evidence(self) -> None:
        result = self.graph.index_paper(self.paper)
        self.assertTrue(result["ok"])
        self.assertGreater(result["entities"], 0)
        self.assertEqual(self.graph.stats()["papers"], 1)
        self.assertEqual(self.graph.search_nodes("fermentation")[0]["name"], "fermentation")

        paper_graph = self.graph.paper_graph("paper_a")
        relations = {edge["relation"] for edge in paper_graph["edges"]}
        self.assertIn("IMPROVES", relations)
        self.assertIn("REDUCES", relations)
        scientific = [edge for edge in paper_graph["edges"] if edge["relation"] == "IMPROVES"]
        self.assertTrue(all(edge["evidence"] for edge in scientific))

    def test_reviewed_fact_and_doi_identity_merge(self) -> None:
        self.graph.index_paper(self.paper)
        reviewed = self.graph.add_reviewed_facts(
            "paper_a",
            [
                {
                    "source": "fermentation",
                    "source_type": "PROCESS",
                    "relation": "IMPROVES",
                    "target": "amino acid quality",
                    "target_type": "TRAIT",
                    "evidence": "Fermentation improved the measured amino acid quality in the study.",
                    "confidence": 0.9,
                }
            ],
        )
        self.assertTrue(reviewed["ok"])
        self.assertEqual(reviewed["added"], 1)
        self.assertGreater(self.graph.stats()["llm_reviewed_edges"], 0)

        cited = {
            "paper_id": "paper_b",
            "title": "The cited paper now ingested",
            "doi": "10.5555/cited.paper",
            "authors": [],
            "abstract": "Sorghum protein digestibility was measured.",
        }
        self.graph.index_paper(cited)
        with self.graph.connect() as conn:
            rows = conn.execute(
                "SELECT id FROM nodes WHERE type='PAPER' AND identity_key=?",
                ("10 5555 cited paper",),
            ).fetchall()
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
