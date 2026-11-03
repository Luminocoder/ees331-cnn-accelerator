"""Our original single-scale INT8 CNN, with the peer DISPLAY/two-digit interface."""
import hashlib

import numpy as np

import mnist_camera_input as samples


def fuse_logits(logit_list):
    # Same temperature and confidence weighting as the v4.4 submission.
    probs = []
    for logits in logit_list:
        z = np.asarray(logits, np.float64)
        exp = np.exp((z-z.max())/1000.0)
        probs.append(exp/exp.sum())
    weights = np.array([p.max() for p in probs])
    weights /= weights.sum()
    return sum(p*w for p, w in zip(probs, weights))


def recognize(board, items, run, resource_root, strategy="single", compare_models=False, update_display=True):
    if strategy != "single" or compare_models:
        raise ValueError("本版使用原版单尺度CNN；融合和MLP不参与识别")
    if board.mode == 'board' and hasattr(board, 'set_capture_display'):
        board.set_capture_display(deferred=not update_display)
    results = []
    for index, item in enumerate(items):
        routes = item.get("variants", [{"fit": 20, "canvas": item["canvas"]}])
        if strategy == "single":
            routes = [routes[0]]
        raw, total_ms = [], 0.0
        for route in routes:
            digit, logits, where, ms = board.infer(route["canvas"])
            raw.append({"fit": route["fit"], "digit": digit, "logits": logits,
                        "pixels_sha256": hashlib.sha256(route["canvas"].tobytes()).hexdigest()})
            total_ms += ms
        digit = int(np.argmax(raw[0]["logits"]))
        display_scores = ([int(round(float(p)*10000)) for p in scores]
                          if strategy == "fused" else raw[0]["logits"])
        meta = dict(item["meta"], score_kind="fusion_score_x10000" if strategy == "fused" else "raw_int32_logits")
        result = {"digit": digit, "where": where, "ms": total_ms, "strategy": strategy,
                  "raw_routes": raw, "fusion_scores": None,
                  "calibrated_probability": False, "display_scores": display_scores,
                  "canvas": item["canvas"], "meta": meta}
        results.append(result)
    # One DISPLAY command after every digit and scale has completed. In two-digit
    # mode this writes the combined decimal value, never just the last digit.
    expected = int("".join(str(r["digit"]) for r in results))
    run.data["partial_results"] = serializable(results)
    run.save()
    shown = board.set_display(expected) if update_display and board.mode == "board" else None
    if update_display and board.mode == "board" and shown != expected:
        raise RuntimeError("DISPLAY回读不一致：请求%d，回读%s" % (expected, shown))
    run.data["display"] = {"requested": expected, "ack": shown,
                           "verified": update_display and board.mode == "board" and shown == expected,
                           "deferred": not update_display,
                           "physical_observation_pending": update_display and board.mode == "board"}
    for result in results:
        result["display"] = run.data["display"]
    return results


def serializable(results):
    return [{k: v for k, v in result.items() if k not in ("canvas", "meta")}
            for result in results]
