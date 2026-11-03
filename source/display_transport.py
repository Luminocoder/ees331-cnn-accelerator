"""Page changes wait five seconds after a verified board ACK; UART runs off Tk."""
from batch_capture import PAGE_SECONDS, PagePlayer, validate_page


class AcknowledgedPagePlayer(PagePlayer):
    def play(self, pages, send, submit, on_request, on_page, on_done, on_error):
        self.stop()
        pages = tuple(validate_page(p) for p in pages)
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
            page = pages[index]
            on_request(index, page, len(pages))

            def work():
                if generation != self.generation:
                    return None
                return send(page)

            def received(ack, error):
                if generation != self.generation:
                    return
                if error is not None or ack != page:
                    self.stop()
                    on_error(index, page, error or RuntimeError('Page ACK mismatch'))
                    return
                on_page(index, page, len(pages))
                if generation == self.generation:
                    self.job = self.scheduler.after(PAGE_SECONDS * 1000, lambda: advance(index+1))

            submit(work, received)

        advance(0)
