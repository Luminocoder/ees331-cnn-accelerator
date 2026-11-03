"""Capture a DECXIN photo, prepare one MNIST-like digit, and send via draft UART."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import secrets
import sys

import cv2
import numpy as np

# The local embedded Python enables safe_path and omits the script directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mnist_serial import image_packet, send_image


REPRESENTATION = "uint8_0_255_before_arm_shift"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_image(path):
    data = Path(path).read_bytes()
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None:
        raise ValueError(f"Cannot decode image: {path}")
    return image


def write_png(path, image):
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("PNG encoding failed")
    with Path(path).open("xb") as output:
        output.write(encoded.tobytes())


def write_json(path, data):
    with Path(path).open("x", encoding="utf-8") as output:
        json.dump(data, output, indent=2, ensure_ascii=True, allow_nan=False)
        output.write("\n")


def crop_roi(image, roi=None):
    if roi is None:
        return image.copy()
    x, y, width, height = roi
    if min(x, y) < 0 or min(width, height) <= 0:
        raise ValueError("ROI requires nonnegative x/y and positive width/height")
    if x + width > image.shape[1] or y + height > image.shape[0]:
        raise ValueError("ROI extends outside the selected image")
    return image[y:y + height, x:x + width].copy()


def select_eye(frame, eye):
    if frame is None or frame.dtype != np.uint8 or frame.shape[:2] != (800, 2560):
        raise ValueError("Expected a uint8 DECXIN combined frame of 2560x800")
    if frame.ndim not in (2, 3) or (frame.ndim == 3 and frame.shape[2] != 3):
        raise ValueError("Expected grayscale or BGR camera pixels")
    if eye not in ("first", "second"):
        raise ValueError("Choose first or second half; physical left/right is unverified")
    start = 0 if eye == "first" else 1280
    return frame[:, start:start + 1280].copy()


def prepare_digit(image, roi=None, polarity="dark"):
    if image is None or image.dtype != np.uint8 or image.size == 0:
        raise ValueError("Input must be a nonempty uint8 image")
    if image.ndim == 3 and image.shape[2] == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    elif image.ndim == 2:
        gray = image.copy()
    else:
        raise ValueError("Only grayscale and three-channel BGR images are supported")
    gray = crop_roi(gray, roi)
    if min(gray.shape) < 5 or int(gray.max()) - int(gray.min()) < 8:
        raise ValueError("No usable digit contrast; select a closer ROI")
    if polarity == "auto":
        border = np.concatenate((gray[0], gray[-1], gray[:, 0], gray[:, -1]))
        polarity = "dark" if float(np.median(border)) >= 127.5 else "light"
    if polarity not in ("dark", "light"):
        raise ValueError("Polarity must be dark, light, or auto")
    ink = 255 - gray if polarity == "dark" else gray
    # Remove illumination before thresholding: a shadow is not digit ink.
    kernel_size = max(31, min(61, min(gray.shape) // 4 | 1))
    background = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
                                 cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                                           (kernel_size, kernel_size)))
    contrast = cv2.subtract(ink, background)
    otsu, _ = cv2.threshold(contrast, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    median = float(np.median(contrast))
    noise_mad = float(np.median(np.abs(contrast.astype(np.float32) - median)))
    threshold = max(12.0, otsu, median + 8 * 1.4826 * noise_mad)
    mask = (contrast > threshold).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    fraction = cv2.countNonZero(mask) / mask.size
    if not 0.0001 <= fraction <= 0.4:
        raise ValueError("Empty/overfilled foreground; check ROI and polarity")
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    minimum_area = max(8, math.ceil(mask.size * 0.0001))
    components = sorted((i for i in range(1, count)
                         if stats[i, cv2.CC_STAT_AREA] >= minimum_area),
                        key=lambda i: int(stats[i, cv2.CC_STAT_AREA]), reverse=True)
    if not components:
        raise ValueError("No digit-sized component found")
    chosen = components[0]
    area = int(stats[chosen, cv2.CC_STAT_AREA])
    if len(components) > 1 and stats[components[1], cv2.CC_STAT_AREA] >= area * 0.25:
        raise ValueError("Multiple large components; crop a single connected digit")
    x, y, width, height, _ = map(int, stats[chosen])
    if (x <= 1 or y <= 1 or x + width >= gray.shape[1] - 1 or
            y + height >= gray.shape[0] - 1):
        raise ValueError("Digit touches ROI boundary; leave background around it")
    foreground = (labels == chosen).astype(np.uint8) * 255
    detected_bbox = [x, y, width, height]
    # Normalize hairline pen strokes before area downsampling can erase them.
    distance = cv2.distanceTransform(foreground, cv2.DIST_L2, 5)
    ridge = (distance > 0) & (distance >= cv2.dilate(distance, np.ones((3, 3), np.uint8)) - 1e-5)
    stroke = max(1.0, float(2 * np.median(distance[ridge]) - 1))
    scale = 20 / max(width, height)
    target_stroke = 2.25
    radius = max(0, math.ceil((target_stroke / scale - stroke) / 2))
    radius = min(radius, math.ceil(max(width, height) * .06))
    # Raw boundary checks above reject clipped captures. Add independent padding
    # so synthetic stroke growth cannot be clipped by the camera ROI itself.
    padding = radius + 2
    origin = [x - padding, y - padding]
    foreground = cv2.copyMakeBorder(foreground[y:y + height, x:x + width],
                                   padding, padding, padding, padding,
                                   cv2.BORDER_CONSTANT, value=0)
    if radius:
        foreground = cv2.dilate(foreground, cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1)))
    x, y, width, height = cv2.boundingRect(foreground)
    digit = foreground[y:y + height, x:x + width]
    scale = 20 / max(width, height)
    scaled_width, scaled_height = max(1, round(width * scale)), max(1, round(height * scale))
    interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    resized = cv2.resize(digit, (scaled_width, scaled_height), interpolation=interpolation)
    canvas = np.zeros((28, 28), np.uint8)
    left, top = (28 - scaled_width) // 2, (28 - scaled_height) // 2
    canvas[top:top + scaled_height, left:left + scaled_width] = resized
    moments = cv2.moments(canvas)
    if moments["m00"] <= 0:
        raise ValueError("Digit disappeared during resizing")
    dx = round(13.5 - moments["m10"] / moments["m00"])
    dy = round(13.5 - moments["m01"] / moments["m00"])
    ys, xs = np.nonzero(canvas)
    dx = max(-int(xs.min()), min(dx, 27 - int(xs.max())))
    dy = max(-int(ys.min()), min(dy, 27 - int(ys.max())))
    canvas = cv2.warpAffine(canvas, np.float32([[1, 0, dx], [0, 1, dy]]),
                            (28, 28), flags=cv2.INTER_NEAREST, borderValue=0)
    metadata = {"polarity": polarity, "roi_xywh": roi,
                "digit_bbox_in_roi_xywh": [origin[0] + x, origin[1] + y, width, height],
                "detected_bbox_in_roi_xywh": detected_bbox,
                "foreground_origin_in_roi_xy": origin,
                "normalization_padding_px": padding,
                "target_stroke_width_28px": target_stroke,
                "otsu_threshold": otsu, "contrast_threshold": threshold,
                "background_noise_mad": noise_mad,
                "background_kernel_size": kernel_size, "component_area": area,
                "stroke_width_source_px": stroke, "stroke_dilation_radius_px": radius,
                "foreground_fraction": fraction, "center_shift_xy": [dx, dy],
                "algorithm": "local-background-padded-stroke2.25-fit20-mass-center-v3",
                "accuracy_validated": False}
    return canvas, foreground, metadata


def save_sample(image, output, metadata, foreground=None):
    output = Path(output)
    pixels = image.tobytes(order="C")
    if image.shape != (28, 28) or image.dtype != np.uint8:
        raise ValueError("Sample must be 28x28 uint8")
    output.mkdir(parents=True, exist_ok=False)
    write_png(output / "mnist28.png", image)
    if foreground is not None:
        write_png(output / "foreground.png", foreground)
    with (output / "pixels-u8.bin").open("xb") as stream:
        stream.write(pixels)
    metadata = {**metadata, "schema": "mnist-camera-sample-v1", "shape_hw": [28, 28],
                "representation": REPRESENTATION, "byte_order": "row-major",
                "pixels_sha256": sha256(pixels), "payload_size": len(pixels),
                "arm_operation": "cnn_write_image applies pixel >> 1 exactly once",
                "created_utc": datetime.now(timezone.utc).isoformat()}
    write_json(output / "sample.json", metadata)
    return metadata


def load_sample(directory):
    directory = Path(directory)
    metadata = json.loads((directory / "sample.json").read_text(encoding="utf-8"))
    if (metadata.get("schema") != "mnist-camera-sample-v1" or
            metadata.get("representation") != REPRESENTATION or
            metadata.get("shape_hw") != [28, 28] or metadata.get("byte_order") != "row-major"):
        raise ValueError("Unknown sample format or quantized input; prepare a uint8 sample")
    pixels = (directory / "pixels-u8.bin").read_bytes()
    if (len(pixels) != 784 or metadata.get("payload_size") != 784 or
            sha256(pixels) != metadata.get("pixels_sha256")):
        raise ValueError("Sample pixel length/hash does not match its manifest")
    return pixels


def interactive_roi(image):
    try:
        roi = tuple(map(int, cv2.selectROI("Digit ROI", image, showCrosshair=True)))
    finally:
        cv2.destroyAllWindows()
    if roi[2] == 0 or roi[3] == 0:
        raise ValueError("ROI selection cancelled; nothing prepared")
    return roi


def capture(args):
    if Path(args.output).exists():
        raise FileExistsError("Output directory already exists; choose a new path")
    camera = cv2.VideoCapture(args.camera_index, cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_V4L2)
    try:
        if not camera.isOpened():
            raise ValueError("Cannot open camera; check its index and close other camera apps")
        settings = {}
        for name, prop, value in (("MJPG", cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG")),
                                  ("width", cv2.CAP_PROP_FRAME_WIDTH, 2560),
                                  ("height", cv2.CAP_PROP_FRAME_HEIGHT, 800),
                                  ("fps", cv2.CAP_PROP_FPS, args.fps)):
            settings[name] = bool(camera.set(prop, value))
        frame = None
        for _ in range(args.warmup_frames):
            ok, candidate = camera.read()
            if ok and candidate is not None:
                select_eye(candidate, args.eye)
                frame = candidate
        if frame is None:
            raise ValueError("Camera returned no valid 2560x800 frame")
        if not args.headless:
            print("Position one digit. SPACE takes a photo; ESC cancels. No images saved until SPACE.")
            while True:
                cv2.imshow("DECXIN " + args.eye + " half", select_eye(frame, args.eye))
                key = cv2.waitKey(20) & 0xFF
                if key == 27 or cv2.getWindowProperty("DECXIN " + args.eye + " half", cv2.WND_PROP_VISIBLE) < 1:
                    raise ValueError("Capture cancelled; nothing saved")
                if key == 32:
                    break
                ok, frame = camera.read()
                if not ok:
                    raise ValueError("Camera stopped delivering frames")
        eye = select_eye(frame, args.eye)
        output = Path(args.output)
        output.mkdir(parents=True, exist_ok=False)
        write_png(output / "raw-stereo.png", frame)
        write_png(output / "eye.png", eye)
        write_json(output / "capture.json", {
            "camera_index": args.camera_index, "index_is_permanent_identity": False,
            "eye_half": args.eye, "physical_left_right_verified": False,
            "shape": list(frame.shape), "dtype": str(frame.dtype),
            "requested_fps": args.fps, "settings_accepted": settings,
            "measured_fps": None, "usb_wire_format_verified": False,
            "created_utc": datetime.now(timezone.utc).isoformat()})
        return {"status": "photo_saved", "output": str(output.resolve())}
    finally:
        camera.release()
        if not args.headless:
            cv2.destroyAllWindows()


def prepare(args):
    source = Path(args.input)
    image = read_image(source)
    roi = interactive_roi(image) if args.select_roi else args.roi
    result, foreground, metadata = prepare_digit(image, roi, args.polarity)
    metadata.update({"source_file": str(source.resolve()),
                     "source_sha256": sha256(source.read_bytes())})
    save_sample(result, args.output, metadata, foreground)
    return {"status": "prepared_not_classified", "output": str(Path(args.output).resolve()),
            "payload_bytes": 784, "representation": REPRESENTATION}


def send(args):
    pixels = load_sample(args.sample)
    sequence = args.sequence if args.sequence is not None else secrets.randbits(32)
    packet = image_packet(pixels, sequence)
    if args.dry_run:
        if args.port or args.acknowledge_draft_protocol:
            raise ValueError("Dry run does not accept a serial port or protocol acknowledgement")
        if args.packet_output:
            with Path(args.packet_output).open("xb") as output:
                output.write(packet)
        return {"status": "dry_run_no_serial_opened", "packet_bytes": len(packet),
                "packet_sha256": sha256(packet), "sequence": sequence,
                "payload_bytes": len(pixels), "protocol_status": "draft_not_team_approved"}
    if not args.port or not args.acknowledge_draft_protocol:
        raise ValueError("Real send requires --port and --acknowledge-draft-protocol; board receiver is not supplied")
    if args.packet_output:
        raise ValueError("--packet-output is only valid with --dry-run")
    try:
        import serial
    except ImportError as exc:
        raise ValueError("pyserial is missing; install scripts/requirements-camera.txt") from exc
    with serial.Serial(port=None, baudrate=args.baud, timeout=0.05,
                       write_timeout=min(0.5, args.timeout), bytesize=8,
                       parity="N", stopbits=1, xonxoff=False, rtscts=False, dsrdtr=False) as port:
        port.dtr, port.rts = False, False
        port.port = args.port
        port.open()
        return send_image(port, pixels, sequence, args.timeout)


def positive_int(value):
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("Must be positive")
    return result


def nonnegative_int(value):
    result = int(value)
    if result < 0:
        raise argparse.ArgumentTypeError("Must be nonnegative")
    return result


def timeout_seconds(value):
    result = float(value)
    if not math.isfinite(result) or not 0 < result <= 60:
        raise argparse.ArgumentTypeError("Must be finite and in (0,60]")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    photo = sub.add_parser("capture", help="Take a photo; physical eye order must be checked")
    photo.add_argument("--camera-index", type=nonnegative_int, required=True)
    photo.add_argument("--eye", choices=("first", "second"), required=True)
    photo.add_argument("--output", required=True)
    photo.add_argument("--fps", type=positive_int, default=30, help="Requested, not measured FPS")
    photo.add_argument("--warmup-frames", type=positive_int, default=8)
    photo.add_argument("--headless", action="store_true", help="Save one frame without a preview window")
    photo.set_defaults(action=capture)
    prep = sub.add_parser("prepare", help="Prepare one isolated digit from a saved image")
    prep.add_argument("--input", required=True)
    prep.add_argument("--output", required=True)
    crop = prep.add_mutually_exclusive_group()
    crop.add_argument("--roi", type=int, nargs=4, metavar=("X", "Y", "W", "H"))
    crop.add_argument("--select-roi", action="store_true")
    prep.add_argument("--polarity", choices=("dark", "light", "auto"), default="dark")
    prep.set_defaults(action=prepare)
    tx = sub.add_parser("send", help="Dry-run or send using the experimental serial protocol")
    tx.add_argument("--sample", required=True)
    tx.add_argument("--dry-run", action="store_true")
    tx.add_argument("--packet-output")
    tx.add_argument("--port")
    tx.add_argument("--baud", type=positive_int, default=115200)
    tx.add_argument("--timeout", type=timeout_seconds, default=3.0)
    tx.add_argument("--sequence", type=nonnegative_int)
    tx.add_argument("--acknowledge-draft-protocol", action="store_true")
    tx.set_defaults(action=send)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(args.action(args), indent=2, allow_nan=False))
        return 0
    except (ValueError, OSError, TimeoutError, cv2.error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancelled", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
