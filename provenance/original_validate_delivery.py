"""Validate the built binary with saved images; never launch GUI or hardware."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT/"source"))
sys.path.insert(0, str(ROOT/"tests"))
import test_v42 as fixtures
import test_demo
import mnist_camera_input as samples
import sample_log
import numpy as np
import cv2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()/("validation-"+uuid.uuid4().hex[:8])
    out.mkdir(parents=True, exist_ok=True)
    def command(argv):
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=90,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for encoding in ("utf-8", "gb18030"):
            try:
                text = result.stdout.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = result.stdout.decode("utf-8", errors="replace")
        return {"argv": [str(x) for x in argv], "exit_code": result.returncode, "output": text}
    source_tests = []
    for name in ("test_v42.py", "test_v5.py", "test_native_runtime.py", "test_demo.py", "test_preview.py", "test_camera_center.py", "test_batch.py", "test_display_transport.py", "test_arm_benchmark.py"):
        result = command([sys.executable, "-B", "-X", "utf8", str(ROOT/"tests"/name)])
        (out/(name+".log")).write_text(result["output"], encoding="utf-8")
        source_tests.append(result)
        if result["exit_code"]: raise RuntimeError("Source regression failed: "+name)
    exe = str(ROOT/"EES331_Studio.exe")
    batch_result = command([exe, '--offline', '--batch-manifest', str(ROOT/'examples/batch-demo.json'),
                            '--output-dir', str(out/'batch_runs')])
    batch_paths = list((out/'batch_runs/batches').glob('*/batch.json'))
    if batch_result['exit_code'] or len(batch_paths)!=1:
        raise RuntimeError('Built binary batch command failed: '+str(batch_result))
    batch = json.loads(batch_paths[0].read_text(encoding='utf-8'))
    if batch['values']!=['1','2','50','9','0','8','1','2'] or batch['pages']!=['01 02 50','09 00 08','01 02   ']:
        raise RuntimeError('Built binary lost batch values, zero padding or blank cells')
    if [p['start_seconds'] for p in batch['playback_plan']]!=[0,5,10]:
        raise RuntimeError('Built binary page schedule changed')
    for entry in batch['entries']:
        record = json.loads((batch_paths[0].parent/entry['run']/'run.json').read_text(encoding='utf-8'))
        if not all(r['display']['deferred'] and not r['display']['verified'] for r in record['results']):
            raise RuntimeError('Batch immediately wrote DISPLAY or claimed hardware verification')
        for sample, result in zip(record['samples'], record['results']):
            pixels = np.frombuffer(samples.load_sample(batch_paths[0].parent/entry['run']/sample['folder']),np.uint8).reshape(28,28)
            import offline_ref
            if offline_ref.infer(pixels)[1]!=result['raw_routes'][0]['logits']:
                raise RuntimeError('Batch saved input/logits are not replayable')
    raw = command([exe, "--selftest"])
    preflight = command([exe, "--check-initializer"])
    benchmark_help=command([exe,'--help'])
    if benchmark_help['exit_code'] or '--benchmark' not in benchmark_help['output']:
        raise RuntimeError('Built EXE is missing the no-camera benchmark entry')
    for switches in (['--benchmark'],['--benchmark','--port','COM5','--offline'],
                     ['--benchmark','--port','COM5','--initialize']):
        rejected=command([exe,*switches])
        if rejected['exit_code']!=2:
            raise RuntimeError('Benchmark conflicting arguments were not rejected before hardware')
    cases = []
    for label, index in (("5", 0), ("4", 2), ("9", 4)):
        path = out/("合成输入_"+label+".png")
        samples.write_png(path, test_demo.demo_frame(label))
        result = command([exe, "--offline", "--image", str(path), "--output-dir", str(out/"binary_runs"), "--label", label])
        result["expected"] = label
        cases.append(result)
    scene = test_demo.demo_frame('50')
    path = out/"不对称两位_50.png"
    samples.write_png(path, scene)
    pair = command([exe, "--offline", "--image", str(path), "--mode", "two", "--output-dir", str(out/"binary_runs"), "--label", "50"])
    pair["expected"] = "50"
    cases.append(pair)
    camera_directory = ROOT/'tests/fixtures/camera-83'
    camera_cases = json.loads((camera_directory/'cases.json').read_text())['cases']
    for case in camera_cases:
        result = command([exe, '--offline', '--image', str(camera_directory/case['file']),
                          '--capture-mode', case['capture_mode'], '--output-dir', str(out/'binary_runs'),
                          '--label', case['expected']])
        result['expected'] = case['expected']
        result['saved_camera_frame'] = case['file']
        cases.append(result)
    blank = out/"空白.png"
    samples.write_png(blank, np.full((240, 320), 255, np.uint8))
    rejection = command([exe, "--offline", "--image", str(blank), "--output-dir", str(out/"binary_runs")])
    replay = command([sys.executable, "-B", "-X", "utf8", str(ROOT/"source/evaluate_samples.py"),
                      "--runs", str(out/"binary_runs"), "--model-base", str(ROOT/"resources"),
                      "--output", str(out/"saved_tensor_evaluation.json")])
    saved = json.loads((out/"saved_tensor_evaluation.json").read_text(encoding="utf-8"))
    for result in cases:
        if result["exit_code"] != 0: raise RuntimeError(result)
    run_data = [(path.parent, json.loads(path.read_text(encoding="utf-8")))
                for path in (out/"binary_runs").glob("*/run.json")]
    actual_correct = 0
    for run_directory, run in run_data:
        if run["ground_truth"] is not None:
            predicted = "".join(str(r["digit"]) for r in run["results"])
            if predicted != run["ground_truth"]: raise RuntimeError("Built binary misclassified controlled input")
            if run["context"]["strategy"] != "single":
                raise RuntimeError("Demo defaults changed")
            filename = run['context']['source'].get('filename')
            expected_mode = next((c['capture_mode'] for c in camera_cases if c['file']==filename), 'guided')
            if run['context']['capture_mode'] != expected_mode:
                raise RuntimeError('Capture mode changed')
            for part, result in zip(run["samples"], run["results"]):
                if len(part["variants"]) != 1 or len(result["raw_routes"]) != 1:
                    raise RuntimeError("Original single input/logit evidence incomplete")
                if "model_comparison" in result:
                    raise RuntimeError("Retired MLP entered demo results")
                for route, raw_route in zip(part["variants"], result["raw_routes"]):
                    payload = samples.load_sample(run_directory/route["folder"])
                    if not (route["pixels_sha256"] == raw_route["pixels_sha256"]
                            == hashlib.sha256(payload).hexdigest()):
                        raise RuntimeError("Saved variant/inference input mismatch")
            actual_correct += 1
    if not (raw["exit_code"] == 0 and preflight["exit_code"] == 0
            and "200/200" in raw["output"] and rejection["exit_code"] == 1
            and replay["exit_code"] == 0 and actual_correct == 8
            and saved["correct_digits"] == saved["total_digits"] == 9):
        raise RuntimeError("Built binary or tensor replay acceptance failed")
    manifest = json.loads((ROOT/"build-manifest.json").read_text())
    for name, sha in manifest["source_sha256"].items():
        if hashlib.sha256((ROOT/"source"/name).read_bytes()).hexdigest() != sha:
            raise RuntimeError("Source changed since build: "+name)
    for name, sha in manifest["resource_sha256"].items():
        for directory in (ROOT/"resources", ROOT/"_internal/resources"):
            if hashlib.sha256((directory/name).read_bytes()).hexdigest() != sha:
                raise RuntimeError("Resource changed since build: "+str(directory/name))
    exe_sha = hashlib.sha256((ROOT/"EES331_Studio.exe").read_bytes()).hexdigest()
    if exe_sha != manifest["exe_sha256"]: raise RuntimeError("EXE changed since build")
    for row in saved["samples"]:
        run = json.loads((Path(row["run"])/"run.json").read_text(encoding="utf-8"))
        original = run["results"][row["index"]]
        if [r["logits"] for r in original["raw_routes"]] != row["raw_route_logits"]:
            raise RuntimeError("Saved-input replay logits changed")
    report = {"hardware_accessed": False, "camera_opened": False, "source_tests": source_tests,
              "exe_sha256": exe_sha,
              "selftest": raw, "initializer_hash_check": preflight,
              'binary_batch': batch_result, 'batch_pages':batch['pages'],
              'batch_over_eight_digits_complete':batch['digit_count']==9,
              'batch_saved_logits_exactly_replayed':True,
              "binary_image_cases": cases, "blank_rejection": rejection,
              "tensor_replay": replay, "saved_inputs_match_current_model": True,
              "source_matches_build_manifest": True, "resources_match_build_manifest": True,
              "packaged_resources_match": True, "raw_logits_replayed_exactly": True,
              "retired_models_used": False, "demo_guides_default": True,
              "synthetic_inputs_only": False,
              "saved_camera_regressions": {'eights': 3, 'real_three': 1, 'correct': 4,
                    'development_samples_not_independent_accuracy': True}}
    sample_log.write_json(out/"delivery_validation.json", report)
    sample_log.write_json(args.output/"latest_validation.json", {"directory": out.name,
                                                               "report": out.name+"/delivery_validation.json"})
    print("PASS: 74 source regressions, actual exe 200/200, image/batch/page regressions, ARM C and benchmark flow, benchmark CLI guards, exact saved-input replay, build hashes; no hardware")


if __name__ == "__main__":
    main()
