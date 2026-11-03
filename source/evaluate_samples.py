"""Replay saved inputs against CNN/fusion and optional MLPs; no reprocessing."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import inference_pipeline
import mnist_camera_input as samples
import model_compare
import offline_ref
import sample_log


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--model-base", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--strategy", choices=("recorded", "single", "fused"), default="recorded")
    parser.add_argument("--include-mlp", action="store_true")
    args = parser.parse_args()
    confusion = np.zeros((10, 10), dtype=int)
    rows, rejected = [], []
    mlp_correct = {name: 0 for name in model_compare.MODELS} if args.include_mlp else {}
    for path in sorted(args.runs.rglob("run.json")):
        run = json.loads(path.read_text(encoding="utf-8"))
        truth = run.get("ground_truth")
        if truth is None:
            continue
        parts = run.get("samples", [])
        if len(parts) != len(truth):
            rejected.append({"run": str(path.parent), "truth": truth,
                             "reason": run.get("error") or "没有完整的预处理输入"})
            continue
        strategy = run["context"].get("strategy", "single") if args.strategy == "recorded" else args.strategy
        for index, part in enumerate(parts):
            raw = samples.load_sample(path.parent/part["folder"])
            canvas = np.frombuffer(raw, np.uint8).reshape(28, 28)
            routes = part.get("variants") if strategy == "fused" else None
            if strategy == "fused" and not routes:
                raise ValueError("记录缺少多尺度输入，不能重新预处理或假装回放融合："+str(path))
            routes = routes or [part]
            route_logits = []
            for route in routes:
                payload = samples.load_sample(path.parent/route["folder"])
                _, logits = offline_ref.infer(np.frombuffer(payload, np.uint8).reshape(28, 28), base=args.model_base)
                route_logits.append(logits)
            digit = int(np.argmax(inference_pipeline.fuse_logits(route_logits)))
            label = int(truth[index])
            confusion[label, digit] += 1
            row = {"run": str(path.parent), "index": index, "label": label, "pred": digit,
                   "pixels_sha256": part["pixels_sha256"], "strategy": strategy,
                   "raw_route_logits": route_logits}
            if args.include_mlp:
                row["mlp"] = {key: model_compare.predict(canvas, args.model_base/"mlp"/name)
                              for key, name in model_compare.MODELS.items()}
                for key, result in row["mlp"].items():
                    mlp_correct[key] += int(result["digit"] == label)
            rows.append(row)
    correct = sum(r["label"] == r["pred"] for r in rows)
    report = {"hardware_accessed": False, "input_reprocessed": False,
              "model_fingerprint": sample_log.model_fingerprint(args.model_base),
              "correct_digits": correct, "total_digits": len(rows), "mlp_correct_digits": mlp_correct,
              "rejected_labelled_runs": rejected, "confusion_true_rows_pred_columns": confusion.tolist(),
              "four_as_nine": int(confusion[4, 9]), "nine_as_four": int(confusion[9, 4]), "samples": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    sample_log.write_json(args.output, report)
    print("已评估%d个数字，CNN正确%d，MLP正确数%s，拒绝记录%d" % (len(rows), correct, mlp_correct, len(rejected)))
    return 0 if rows else 2


if __name__ == "__main__":
    raise SystemExit(main())
