"""Explicit board acceptance: frozen 20 images / 200 logits + DISPLAY replies."""
import json
from pathlib import Path
import time
import uuid

import numpy as np

import sample_log


def run(board, port, resources, output_root):
    directory = Path(output_root)/(time.strftime("%Y%m%d-%H%M%S-")+uuid.uuid4().hex[:8])
    directory.mkdir(parents=True, exist_ok=False)
    report = {"status": "RUNNING", "port": port, "initialization_executed": False,
              "model": "FPGA INT8 CNN", "model_fingerprint_pc_reference": sample_log.model_fingerprint(resources),
              "samples": [], "matched_logits": 0, "total_logits": 200,
              "hardware_model_attested": False, "physical_display_observation_pending": True}
    try:
        frozen = Path(resources)/"samples/_frozen"
        expected = np.loadtxt(frozen/"expected_scores.txt", dtype=np.int64)
        labels = [int(v) for v in (frozen/"labels.txt").read_text().split()]
        if expected.shape != (20, 10) or len(labels) != 20:
            raise ValueError("Expected frozen twenty-image / two-hundred-score baseline")
        board.connect(port)
        board.hello()
        if board.mode != "board":
            raise RuntimeError("Board acceptance cannot run on the PC fallback")
        for index, label in enumerate(labels):
            pixels = np.array([[int(v, 16) for v in line.split()] for line in
                               (frozen/("img_%02d.txt" % index)).read_text().splitlines() if line.strip()], np.uint8)
            digit, logits, channel, ms = board.infer(pixels)
            matched = sum(a == b for a, b in zip(logits, expected[index].tolist()))
            shown = board.set_display(digit)
            row = {"index": index, "label": label, "digit": digit, "logits": logits,
                   "matched_logits": matched, "channel": channel, "display_ack": shown, "ms": ms}
            report["samples"].append(row)
            report["matched_logits"] += matched
            sample_log.write_json(directory/"results.json", report)
            print("img_%02d: digit=%d label=%d logits=%d/10 DISPLAY_ACK=%s" % (index, digit, label, matched, shown), flush=True)
            if channel != "板子" or matched != 10 or len(logits) != 10 or digit != label or shown != digit:
                raise RuntimeError("Board score/digit/display acceptance failed at img_%02d" % index)
        # Combined display command is also checked, separately from inference.
        report["two_digit_display_ack"] = board.set_display(49)
        if report["two_digit_display_ack"] != 49:
            raise RuntimeError("Two-digit DISPLAY ACK mismatch")
        report["status"] = "BOARD_ACCEPTANCE_PASS"
        print("PASS: 20/20 samples; 200/200 logits; DISPLAY replies verified; physical display still needs observation")
        code = 0
    except Exception as exc:
        report.update(status="BOARD_ACCEPTANCE_FAIL", error=str(exc))
        print("FAIL:", exc)
        code = 1
    finally:
        board.close()
    sample_log.write_json(directory/"results.json", report)
    print("LOG="+str(directory))
    return code
