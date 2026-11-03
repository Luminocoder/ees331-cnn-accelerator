"""Audit clock-only BRAM controls and render a reset window from actual XSim VCD."""
import json
import math
import runpy
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def audit_and_render(folder):
    parser = runpy.run_path(str(Path(__file__).with_name("summarize-system-waveform.py")))
    signals, _ = parser["read_vcd"](folder / "axi-reset-boundaries.vcd")
    value = parser["integer_value"]
    monitored = next(t for t, bits in signals["watch_clock_only_outputs"]["changes"] if bits == "1")
    clock_edges = {t for t, bits in signals["clk"]["changes"] if bits == "1"}
    controls = ("fm_a_we", "fm_a_addr", "fm_a_wdata", "accel_soft_rst")
    violations = [
        {"signal": name, "time_ns": t}
        for name in controls for t, _ in signals[name]["changes"]
        if t >= monitored and not any(math.isclose(t, edge, abs_tol=0.0001) for edge in clock_edges)
    ]
    assertions = [t for t, bits in signals["rst_n"]["changes"] if t > monitored and bits == "0"]
    if len(assertions) != 9:
        raise ValueError(f"Expected 9 reset boundaries, got {len(assertions)}")
    falling = assertions[0]
    sampling = min(t for t in clock_edges if t > falling)
    start, end = falling - 65, sampling + 85
    rows = ("clk", "rst_n", "fm_a_we", "fm_a_addr", "fm_a_wdata", "accel_soft_rst", "bvalid", "fm_write_count")
    image = Image.new("RGB", (1800, 210 + len(rows) * 48), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 18)
    heading = ImageFont.truetype("C:/Windows/Fonts/consola.ttf", 25)
    left, right, top = 250, 1750, 140

    def x(t):
        return left + (t - start) * (right - left) / (end - start)

    title = "AXI/BRAM reset boundary: " + ("FAIL - asynchronous change detected" if violations else "PASS - clock-only controls")
    draw.text((25, 20), title, fill="#172020", font=heading)
    draw.text((25, 58), "Actual XSim VCD | 50 MHz | behavioral BRAM | not post-route simulation or hardware", fill="#4b5555", font=font)
    for index in range(9):
        t = start + index * (end - start) / 8
        draw.line((x(t), top - 5, x(t), top + len(rows) * 48), fill="#e5ebeb")
        draw.text((x(t), top - 25), f"{t:.1f}", font=font, fill="#4b5555", anchor="mm")
    draw.text((right, 88), "Time (ns)", font=font, fill="#4b5555", anchor="rm")
    for index, name in enumerate(rows):
        center = top + index * 48 + 20
        signal = signals[name]
        changes = signal["changes"]
        before = [bits for t, bits in changes if t <= start]
        points = [(start, before[-1] if before else "x")]
        points.extend((t, bits) for t, bits in changes if start < t < end)
        points.append((end, points[-1][1]))
        draw.text((25, center), name, font=font, fill="#172020", anchor="lm")
        previous_y = None
        for (begin, bits), (finish, _) in zip(points, points[1:]):
            number = value(bits, signal["width"])
            color = "#b52c36" if number is None else "#087969"
            if signal["width"] == 1 and number is not None:
                y = center + 10 - number * 20
                if previous_y is not None:
                    draw.line((x(begin), previous_y, x(begin), y), fill=color, width=2)
                draw.line((x(begin), y, x(finish), y), fill=color, width=2)
                previous_y = y
            else:
                draw.rectangle((x(begin), center - 12, x(finish), center + 12), outline=color, width=2)
                label = "X" if number is None else (hex(number) if name.startswith("fm_a_") else str(number))
                if draw.textlength(label, font=font) + 8 < x(finish) - x(begin):
                    draw.text(((x(begin) + x(finish)) / 2, center), label, font=font, fill=color, anchor="mm")
    for t, label, color in ((falling, "reset assertion", "#b52c36"), (sampling, "next clock", "#216dba")):
        draw.line((x(t), top, x(t), top + len(rows) * 48), fill=color, width=2)
        draw.text((x(t), image.height - 52), label, fill=color, font=font, anchor="mm")
    draw.text((25, image.height - 28), "BRAM inputs remain stable until the sampling edge; an issued write is not asynchronously cancelled.", font=font, fill="#4b5555")
    image.save(folder / "axi-reset-boundary.png")
    result = {"status": "FAIL" if violations else "PASS", "reset_assertions_checked": len(assertions),
              "clock_only_controls": list(controls), "off_clock_changes": violations,
              "shown_reset_ns": falling, "next_clock_ns": sampling, "hardware_verified": False}
    (folder / "axi-reset-waveform-audit.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
    return bool(violations)


if __name__ == "__main__":
    sys.exit(audit_and_render(Path(sys.argv[1])))
