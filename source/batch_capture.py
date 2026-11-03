"""Ordered capture sessions and nonblocking playback of eight display cells."""
from datetime import datetime, timezone
from pathlib import Path
import uuid

import sample_log

PAGE_SECONDS = 5


def display_pages(values):
    values = list(values)
    for text in values:
        if not isinstance(text, str) or len(text) not in (1, 2) or any(c not in '0123456789' for c in text):
            raise ValueError('每个结果需要一位或两位数字字符串')
    normalized = [value.zfill(2) for value in values]
    return [' '.join(normalized[i:i+3]).ljust(8) for i in range(0, len(values), 3)]


def validate_page(text):
    if not isinstance(text, str) or len(text) != 8 or any(c not in '0123456789 ' for c in text):
        raise ValueError('数码管页需要恰好8个数字或空格')
    return text


class BatchSession:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = None
        self.data = {'state': 'idle', 'entries': [], 'pages': [], 'display_events': []}

    @property
    def collecting(self):
        return self.data['state'] == 'collecting'

    @property
    def values(self):
        return [e['text'] for e in self.data['entries'] if e['status'] == 'recognized']

    def save(self):
        self.data['values'] = self.values
        self.data['full_text'] = ' '.join(v.zfill(2) for v in self.values)
        self.data['digit_count'] = sum(len(v) for v in self.values)
        self.data['pages'] = display_pages(self.values)
        self.data['page_count'] = len(self.data['pages'])
        if self.path is not None:
            sample_log.write_json(self.path/'batch.json', self.data)

    def start(self):
        if self.collecting:
            raise ValueError('当前批次尚未结束')
        batch_id = datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8]
        self.path = self.directory/batch_id
        self.path.mkdir(parents=True, exist_ok=False)
        self.data = dict(schema='ees331-capture-batch-v1', batch_id=batch_id,
            created_utc=datetime.now(timezone.utc).isoformat(), state='collecting', entries=[],
            pages=[], page_seconds=PAGE_SECONDS, display_events=[], playback_state='not_started',
            display_contract='8 cells: DD space DD space DD; single digits zero-padded')
        self.save()

    def reserve(self, mode):
        if not self.collecting or mode not in ('single', 'two'):
            raise ValueError('需要正在采集的批次和有效识别模式')
        index = len(self.data['entries'])
        self.data['entries'].append(dict(index=index, mode=mode, status='processing'))
        self.save()
        return dict(batch_id=self.data['batch_id'], index=index)

    def _entry(self, ticket):
        if not self.collecting or ticket['batch_id'] != self.data.get('batch_id'):
            raise ValueError('批次已变化，不能加入旧结果')
        entry = self.data['entries'][ticket['index']]
        if entry['status'] != 'processing':
            raise ValueError('该照片已记录，不能重复加入')
        return entry

    def success(self, ticket, text, run_path):
        entry = self._entry(ticket)
        expected = 2 if entry['mode'] == 'two' else 1
        if len(text) != expected:
            raise ValueError('识别结果位数与拍摄模式不一致')
        display_pages([text])
        entry.update(status='recognized', text=text, display_text=text.zfill(2),
                     run=self._relative_run(run_path))
        self.save()

    def failure(self, ticket, error, run_path=None):
        self._entry(ticket).update(status='error', error=str(error), run=self._relative_run(run_path))
        self.save()

    def _relative_run(self, run_path):
        if run_path is None:
            return None
        import os
        return os.path.relpath(Path(run_path).resolve(), self.path.resolve()).replace('\\', '/')

    def finish(self):
        if not self.collecting:
            raise ValueError('没有正在采集的批次')
        if any(e['status'] == 'processing' for e in self.data['entries']):
            raise ValueError('照片仍在识别，稍后再结束')
        self.data.update(state='completed', finished_utc=datetime.now(timezone.utc).isoformat())
        self.save()
        return list(self.data['pages'])

    def cancel(self):
        if self.collecting:
            for entry in self.data['entries']:
                if entry['status'] == 'processing':
                    entry.update(status='cancelled', error='批次取消')
            self.data['state'] = 'cancelled'
            self.save()

    def record_display(self, state, index=None, page=None, error=None, transport='computer_preview', ack_verified=False):
        self.data['playback_state'] = state
        self.data['display_events'].append(dict(state=state, page_index=index, page=page,
            transport=transport, ack_verified=ack_verified, hardware_verified=False,
            physical_observation_pending=(transport == 'uart'), error=error,
            utc=datetime.now(timezone.utc).isoformat()))
        self.save()


class PagePlayer:
    """One scheduled callback; old replay callbacks cannot advance a new session."""
    def __init__(self, scheduler):
        self.scheduler = scheduler
        self.job = None
        self.generation = 0
        self.playing = False

    def stop(self):
        self.generation += 1
        self.playing = False
        if self.job is not None:
            self.scheduler.after_cancel(self.job)
            self.job = None

    def play(self, pages, on_page, on_done):
        self.stop()
        pages = tuple(validate_page(page) for page in pages)
        generation = self.generation
        self.playing = bool(pages)

        def advance(index):
            if generation != self.generation:
                return
            self.job = None
            if index == len(pages):
                self.playing = False
                on_done()
                return
            on_page(index, pages[index], len(pages))
            if generation == self.generation:
                self.job = self.scheduler.after(PAGE_SECONDS*1000, lambda: advance(index+1))
        advance(0)
