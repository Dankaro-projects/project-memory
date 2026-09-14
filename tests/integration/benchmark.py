"""Local synthetic search measurement. Character counts are not token counts."""

import argparse
import json
from pathlib import Path
import statistics
import time

from memory_module import Memory, dumps


def run(output, count=1000, queries=30):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    with Memory.create(output / "benchmark.sqlite", "Synthetic retrieval measurement", [
        "Use records relevant to the current question.",
        "Read exact source text when a number or exception matters.",
    ]) as memory:
        corpus_chars = 0
        start = time.perf_counter()
        for i in range(count):
            body = f"Reference record {i}. " + ("Routine local test material. " * 60)
            if i == count // 2:
                body = "The bluefin calibration interval is 17 days. Do not apply it to other equipment. " + body
            corpus_chars += len(body)
            memory.source(f"reference-{i}", f"Reference {i}",
                "Bluefin calibration; read exact interval and exception." if i == count//2 else f"Routine record {i}.",
                body, "document")
        insert_ms = (time.perf_counter() - start) * 1000
        latencies = []
        packet = None
        for _ in range(queries):
            start = time.perf_counter()
            packet = memory.context("bluefin calibration", budget=2000)
            latencies.append((time.perf_counter()-start)*1000)
        sources = [r for r in packet["records"] if r["kind"] == "source"]
        assert len(sources) == 1
        exact = memory.read(sources[0]["id"], detail=True)
        assert "17 days" in exact["body"] and "Do not apply it to other equipment" in exact["body"]
        report = {"records": count, "source_body_characters": corpus_chars,
                  "insert_ms": round(insert_ms, 3), "queries": queries,
                  "median_context_ms": round(statistics.median(latencies), 3),
                  "maximum_context_ms": round(max(latencies), 3), "context_characters": len(dumps(packet)),
                  "context_budget_characters": 2000, "returned_source_records": len(sources),
                  "exact_read_characters": len(dumps(exact)),
                  "token_count": None, "llm_calls": 0,
                  "limitations": "Synthetic lexical lookup on this machine. No semantic recall, model-token saving or AI-quality claim."}
        (output / "benchmark-result.json").write_text(json.dumps(report, indent=2) + "\n")
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="benchmark-run")
    print(json.dumps(run(parser.parse_args().output), indent=2))
