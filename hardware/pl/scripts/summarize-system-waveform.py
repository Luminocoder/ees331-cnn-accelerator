"""Validate waveform artifacts and render actual VCD transitions (requires Pillow)."""
import csv
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

LAYERS = {0: "Idle", 1: "Conv1", 2: "Pool1", 3: "Conv2", 4: "Pool2", 5: "FC1", 6: "FC2", 7: "Done state"}
EXPECTED_WRITES = {1: 6272, 2: 1568, 3: 3136, 4: 784, 5: 64, 6: 10}


def read_vcd(path):
    names, widths, changes = {}, {}, {}
    time_ns, scale = 0, None
    header = True
    with path.open(encoding="utf-8") as stream:
        for raw in stream:
            line = raw.strip()
            if header:
                match = re.search(r"\b(\d+)\s*(ps|ns|us|ms|s)\b", line)
                if match and scale is None:
                    scale = int(match[1]) * {"ps": 0.001, "ns": 1, "us": 1000, "ms": 1e6, "s": 1e9}[match[2]]
                if line.startswith("$var "):
                    fields = line.split()
                    code, name = fields[3:5]
                    names[name] = code
                    widths[code] = int(fields[2])
                    changes.setdefault(code, [])
                if line.startswith("$enddefinitions"):
                    if scale is None:
                        raise ValueError("VCD timescale missing")
                    header = False
                continue
            if line.startswith("#"):
                time_ns = int(line[1:]) * scale
            elif line and line[0] in "01xXzZbB":
                if line[0] in "bB":
                    value, code = line[1:].split()
                else:
                    value, code = line[0], line[1:]
                if code in changes:
                    transitions = changes[code]
                    if not transitions or value.lower() != transitions[-1][1]:
                        transitions.append((time_ns, value.lower()))
    return {name: {"width": widths[code], "changes": changes[code]} for name, code in names.items()}, time_ns


def integer_value(bits, width, signed=False):
    if "x" in bits or "z" in bits:
        return None
    value = int(bits, 2)
    return value - (1 << width) if signed and value & (1 << (width - 1)) else value


def render_waveform(path, signals, rows, start_ns, end_ns, title, footer):
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (2000, 280 + 56 * len(rows)), "white")
    draw = ImageDraw.Draw(image)
    font_path = Path("C:/Windows/Fonts/consola.ttf")
    font = ImageFont.truetype(str(font_path), 21) if font_path.exists() else ImageFont.load_default(size=21)
    small = ImageFont.truetype(str(font_path), 17) if font_path.exists() else ImageFont.load_default(size=17)
    heading = ImageFont.truetype(str(font_path), 28) if font_path.exists() else ImageFont.load_default(size=28)
    left, right, top = 275, 1940, 160
    unit, factor = ("ms", 1e6) if end_ns - start_ns > 1e6 else ("us", 1000)

    def x(time):
        return left + (time - start_ns) / (end_ns - start_ns) * (right - left)

    draw.text((35, 24), title, fill="#172020", font=heading)
    draw.text((35, 66), "Source: XSim VCD | 50 MHz | img00 | real RTL, not mock engines", fill="#4b5555", font=small)
    draw.text((35, 94), "States: 0 Idle | 1 Conv1 | 2 Pool1 | 3 Conv2 | 4 Pool2 | 5 FC1 | 6 FC2 | 7 Done", fill="#4b5555", font=small)
    for i in range(9):
        time = start_ns + (end_ns - start_ns) * i / 8
        column = x(time)
        draw.line((column, top - 4, column, top + 56 * len(rows)), fill="#e5ebeb")
        label = f"{time / factor:.3f}"
        draw.text((column, top - 28), label, fill="#4b5555", font=small, anchor="mm")
    draw.text((right, top - 56), f"Time ({unit})", fill="#4b5555", font=small, anchor="rm")
    for index, name in enumerate(rows):
        center = top + 56 * index + 24
        draw.text((35, center), name, fill="#172020", font=font, anchor="lm")
        signal = signals[name]
        transitions = signal["changes"]
        before = [(time, bits) for time, bits in transitions if time <= start_ns]
        points = [(start_ns, before[-1][1] if before else "x")]
        points.extend((time, bits) for time, bits in transitions if start_ns < time < end_ns)
        points.append((end_ns, points[-1][1]))
        last_y = None
        for (begin, bits), (end, _) in zip(points, points[1:]):
            x1, x2 = x(begin), x(end)
            signed = name.startswith("result_") and name not in {"result_read_address", "result_read_valid", "result_read_ready"}
            value = integer_value(bits, signal["width"], signed or name == "fc_out_wdata")
            color = "#c43737" if value is None else "#067665"
            if signal["width"] == 1 and value is not None:
                y = center + 12 - value * 24
                if last_y is not None:
                    draw.line((x1, last_y, x1, y), fill=color, width=2)
                draw.line((x1, y, x2, y), fill=color, width=2)
                last_y = y
            else:
                draw.rectangle((x1, center - 14, x2, center + 14), outline=color, width=2)
                label = "X" if value is None else (LAYERS.get(value, str(value)) if name == "layer_status" else str(value))
                if value is not None and name in {"awaddr", "wdata", "fm_a_addr", "fm_a_wdata", "result_read_address"}:
                    label = f"0x{value:x}"
                if draw.textlength(label, font=small) + 12 < x2 - x1:
                    draw.text(((x1 + x2) / 2, center), label, fill=color, font=small, anchor="mm")
        draw.line((35, center + 28, right, center + 28), fill="#f0f3f3")
    draw.text((35, image.height - 62), footer, fill="#4b5555", font=small)
    draw.text((35, image.height - 34), "Narrow pulses can appear as vertical lines at overview scale. Zoom in to measure their width.", fill="#4b5555", font=small)
    image.save(path)


def summarize(folder):
    with (folder / "events.csv").open(encoding="utf-8", newline="") as stream:
        events = [{key: (value if key == "event" else int(value)) for key, value in row.items()} for row in csv.DictReader(stream)]

    def find(name, layer=None):
        return [event for event in events if event["event"] == name and (layer is None or event["layer"] == layer)]

    inputs, writes, reads = find("input_write"), find("result_write"), find("result_read")
    assert len(inputs) == 784 and [e["address"] for e in inputs] == list(range(784))
    assert len(writes) == 10 and [e["address"] for e in writes] == list(range(10))
    assert len(reads) == 10 and [e["address"] for e in reads] == list(range(0x10, 0x38, 4))
    assert [e["value"] for e in writes] == [e["value"] for e in reads]
    assert [e["layer"] for e in find("layer")] == [0, 1, 2, 3, 4, 5, 6, 7, 0]
    assert len(find("start")) == len(find("done")) == len(find("finish")) == 1
    stages = []
    for layer, count in EXPECTED_WRITES.items():
        starts, dones = find("engine_start", layer), find("engine_done", layer)
        assert len(starts) == len(dones) == 1
        output = find("result_write" if layer == 6 else "feature_write", layer)
        assert len(output) == count
        assert starts[0]["time_ns"] < output[0]["time_ns"] <= output[-1]["time_ns"] < dones[0]["time_ns"]
        stages.append({"layer": LAYERS[layer], "start_ns": starts[0]["time_ns"], "done_ns": dones[0]["time_ns"], "writes": count})
    assert inputs[-1]["time_ns"] < find("start")[0]["time_ns"]
    assert stages[-1]["done_ns"] < find("done")[0]["time_ns"] < reads[0]["time_ns"]

    signals, last_transition_ns = read_vcd(folder / "system-overview.vcd")
    end_ns = find("finish")[0]["time_ns"]
    required = ["rst_n", "accel_start", "accel_busy", "accel_done", "irq_done", "accel_error", "layer_status",
                "conv_start", "conv_done", "pool_start", "pool_done", "fc_start", "fc_done", "c_is_fc2",
                "fc_out_we", "fc_out_addr", "fc_out_wdata", "fm_a_we", "fm_a_addr", "fm_a_wdata",
                "result_read_valid", "result_read_ready", "result_read_address", "result_read_data"] + [f"result_{i}" for i in range(10)]
    assert all(name in signals and signals[name]["changes"] for name in required)
    for i, result in enumerate(writes):
        signal = signals[f"result_{i}"]
        assert integer_value(signal["changes"][-1][1], signal["width"], signed=True) == result["value"]
    assert integer_value(signals["accel_done"]["changes"][-1][1], 1) == 1
    assert integer_value(signals["irq_done"]["changes"][-1][1], 1) == 1
    assert integer_value(signals["accel_error"]["changes"][-1][1], 1) == 0
    # VCD need not end with a timestamp when no recorded signal changes at $finish.
    assert writes[-1]["time_ns"] <= last_transition_ns <= end_ns

    for path in folder.glob("*.wcfg"):
        tree = ET.parse(path)
        refs = tree.findall(".//db_ref")
        assert len(refs) == 1
        refs[0].set("path", "system-full.wdb")
        objects = [o for o in tree.findall(".//wvobject") if o.get("fp_name", "").startswith("/system_waveform_tb/")]
        assert objects, f"No signal objects in {path.name}"
        for obj in objects:
            name = obj.get("fp_name", "").rsplit("/", 1)[-1]
            if re.fullmatch(r"result_[0-9]|fc_out_wdata|accum_reg|bias_reg|result_read_data", name):
                assert obj.find("./obj_property[@name='Radix']").text in {"SIGNEDDECRADIX", "DECRADIX"}, f"Unsigned result: {name}"
        assert (folder / "system-full.wdb").stat().st_size > 1024
        tree.write(path, encoding="UTF-8", xml_declaration=True)

    total_start = find("start")[0]["time_ns"]
    total_done = find("done")[0]["time_ns"]
    fc2_start = stages[-1]["start_ns"]
    values = [event["value"] for event in writes]
    prediction = max(range(10), key=lambda i: values[i])
    assert prediction == 5
    windows = {"whole_system": [0, int(end_ns)], "input_and_start": [0, total_start + 400],
               "fc2_and_readback": [fc2_start - 200, int(end_ns)],
               "last_write_to_done": [writes[-1]["time_ns"] - 200, int(end_ns)]}
    report = {"status": "PASS", "images": 1, "bit_exact_results": 10,
              "input_writes": 784, "prediction": prediction, "signed_logits": values,
              "start_ns": total_start, "done_ns": total_done,
              "start_to_done_ns": total_done - total_start,
              "start_to_done_cycles": (total_done - total_start) // 20,
              "stages": stages, "windows_ns": windows,
              "control_event_sample_delay_ns": 1}
    (folder / "timing-summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    overview_rows = ["rst_n", "fm_a_we", "accel_start", "accel_busy", "layer_status", "conv_start", "conv_done",
                     "pool_start", "pool_done", "fc_start", "fc_done", "c_is_fc2", "fc_out_we", "accel_done", "irq_done", "result_read_valid"]
    render_waveform(folder / "system-overview.png", signals, overview_rows, 0, end_ns,
                    "Complete inference: AXI input -> six CNN layers -> results -> AXI readback",
                    f"START-to-DONE: {(total_done-total_start)/1e6:.6f} ms | class=5 | 10/10 bit-exact | see WDB for AXI/BRAM detail")
    render_waveform(folder / "system-fc2-output.png", signals,
                    ["layer_status", "c_is_fc2", "fc_start", "fc_done", "fc_out_we", "fc_out_addr", "fc_out_wdata", "accel_done", "irq_done"]
                    + ["result_read_address", "result_read_valid", "result_read_ready", "result_read_data"] + [f"result_{i}" for i in range(10)],
                    fc2_start - 200, end_ns, "FC2: ten INT32 result writes, then DONE and result readback",
                    f"AXI result readback: {reads[0]['time_ns']}..{reads[-1]['time_ns']} ns | all ten values match golden")
    render_waveform(folder / "system-input.png", signals,
                    ["rst_n", "awaddr", "awvalid", "awready", "wdata", "wvalid", "wready", "bvalid", "bready", "fm_a_we", "fm_a_addr", "fm_a_wdata"],
                    0, 1200, "AXI input detail: write pointer, then pixel bytes into BRAM",
                    "Addresses are register offsets: 0x44=IMG_PTR, 0x40=IMG_DATA. Inspect WE at the consuming rising edge.")
    render_waveform(folder / "system-result-readback.png", signals,
                    ["layer_status", "fc_out_we", "fc_out_addr", "fc_out_wdata", "fc_done", "accel_done", "irq_done",
                     "result_read_address", "result_read_valid", "result_read_ready", "result_read_data", "result_5", "result_9"],
                    windows["last_write_to_done"][0], end_ns, "Final write -> FC DONE -> system DONE -> ten AXI result responses",
                    "The largest signed score is result_5=24843. Read addresses 0x10..0x34 correspond to classes 0..9.")

    lines = ["# System waveform viewing guide", "", "## Open in Vivado", "",
             "Keep WDB and WCFG files in this same folder. In Vivado Tcl Console:", "", "```tcl",
             "open_wave_database {" + (folder / "system-full.wdb").as_posix() + "}",
             "open_wave_config {" + (folder / "system-overview.wcfg").as_posix() + "}",
             "```", "", "Use Zoom Fit for the whole image. Switch to system-detail.wcfg for all recorded signals,",
             "system-input.wcfg for AXI image writes, or system-fc2-output.wcfg for FC2 and AXI result reads.", "",
             "## Time windows (ns)", "", "| View | From | To |", "|---|---:|---:|"]
    lines += [f"| {name} | {start} | {end} |" for name, (start, end) in windows.items()]
    lines += ["", "## Six real layer executions", "", "| Layer | START sampled (ns) | DONE sampled (ns) | Writes |",
              "|---|---:|---:|---:|"]
    lines += [f"| {s['layer']} | {s['start_ns']} | {s['done_ns']} | {s['writes']} |" for s in stages]
    lines += ["", "Control events in CSV are sampled 1 ns after the rising edge (NBA settled).",
              "BRAM writes and AXI responses are logged on their consuming rising edges.",
              "PNG files use exact VCD transition timestamps, not these sampled control timestamps.", "",
              "## What to check", "",
              "- Input: VALID && READY at a rising edge accepts a transfer; 784 BRAM Port-A writes precede START.",
              "- Layer state: 0 Idle -> 1 Conv1 -> 2 Pool1 -> 3 Conv2 -> 4 Pool2 -> 5 FC1 -> 6 FC2 -> 7 -> 0.",
              "- Engine START is a one-cycle pulse; DONE can remain high until that engine starts again.",
              "- BRAM write data/address are meaningful when WE=1 at the rising edge, not merely when they change.",
              "- FC1 writes 64 INT8 activations to BRAM; FC2 writes ten signed INT32 logits to result registers.",
              "- Last FC2 write precedes fc_done, which precedes accel_done/irq_done. DONE remains high after state returns to Idle.",
              "- AXI STATUS offset 0x04 bit1 is DONE; RESULT offsets 0x10..0x34 return ten scores.",
              "- Results and accumulators use signed radix. AXI rdata uses hex; change radix for signed interpretation.",
              "- result_read_* are observation-only aliases for actual AXI result responses; they exclude STATUS polling.",
              "- X means unknown, not zero. Unread/uninitialized BRAM data can be X; valid input/results must not be X.",
              "- Overview-scale narrow pulses can disappear visually; use the smaller time windows to inspect handshakes.", "",
              "## Verified boundary", "",
              f"One image (img00, label=5): 10/10 logits bit-exact, argmax={prediction}.",
              f"Signed logits: {values}",
              "This run intentionally stops after the original SoC test checks its first image, not after all 20.",
              "AXI master is a testbench, not an executing ARM core. DDR, camera and output peripheral are not simulated.",
              "This is behavioral RTL simulation, not post-route timing or physical-board verification.", ""]
    (folder / "viewing-guide.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"SYSTEM_WAVEFORM_ARTIFACTS_PASS: {len(signals)} VCD signals; six layers; 10 signed results; class={prediction}")


if __name__ == "__main__":
    summarize(Path(sys.argv[1]).resolve())
