"""Offline mixed single/two-digit image batches, using the same GUI pipeline."""
import json
from pathlib import Path

from batch_capture import BatchSession, PAGE_SECONDS
import inference_pipeline
import mnist_camera_input
import sample_log
import vision_pipeline


def run_manifest(manifest, output, board, resources, capture_mode='guided'):
    manifest = Path(manifest).resolve()
    data = json.loads(manifest.read_text(encoding='utf-8'))
    captures = data['captures']
    if not isinstance(captures, list) or not captures:
        raise ValueError('清单需要非空captures数组')
    for capture in captures:
        if not isinstance(capture, dict) or not isinstance(capture.get('image'), str):
            raise ValueError('每条capture需要image文件路径')
        if capture.get('mode', 'single') not in ('single', 'two'):
            raise ValueError('mode必须为single或two')
    if board.mode != 'offline':
        raise ValueError('批次清单仅用于离线，不接受板卡后端')
    session = BatchSession(Path(output)/'batches')
    session.start()
    for capture in captures:
        mode = capture.get('mode', 'single')
        ticket = session.reserve(mode)
        run = None
        try:
            source = (manifest.parent/capture['image']).resolve()
            frame = mnist_camera_input.read_image(source)
            context = dict(mode=mode, source={'kind':'file', 'filename':source.name},
                capture_mode=capture.get('capture_mode', capture_mode), strategy='single',
                batch_ticket=ticket, display_deferred=True, channel='offline',
                pc_reference_model_fingerprint=sample_log.model_fingerprint(resources))
            run = sample_log.SampleRun(session.path/'captures', frame, context)
            items = vision_pipeline.prepare(frame, mode, capture.get('roi'), context['capture_mode'])
            run.add_items(items)
            results = inference_pipeline.recognize(board, items, run, resources, update_display=False)
            run.finish(inference_pipeline.serializable(results))
            session.success(ticket, ''.join(str(r['digit']) for r in results), run.path)
        except Exception as error:
            if run is not None:
                run.finish(error=error)
            session.failure(ticket, error, run.path if run else None)
    pages = session.finish()
    session.data['playback_plan'] = [dict(page_index=i, text=page,
        start_seconds=i*PAGE_SECONDS, duration_seconds=PAGE_SECONDS,
        hardware_verified=False) for i, page in enumerate(pages)]
    session.save()
    return session
