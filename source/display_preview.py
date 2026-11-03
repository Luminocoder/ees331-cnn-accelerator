"""Render digits and genuine blank cells using seven segments per position."""
PATTERNS = {'0':'abcdef', '1':'bc', '2':'abdeg', '3':'abcdg', '4':'bcfg',
            '5':'acdfg', '6':'acdefg', '7':'abc', '8':'abcdefg', '9':'abcdfg', ' ':''}


def draw(canvas, text, color='#35c4e8'):
    from batch_capture import validate_page
    validate_page(text)
    canvas.delete('all')
    for index, char in enumerate(text):
        x, y = 8+index*34, 5
        segments = dict(a=(x+5,y,x+23,y+3), b=(x+23,y+4,x+26,y+23),
            c=(x+23,y+27,x+26,y+46), d=(x+5,y+47,x+23,y+50),
            e=(x+2,y+27,x+5,y+46), f=(x+2,y+4,x+5,y+23),
            g=(x+5,y+23,x+23,y+27))
        for name, box in segments.items():
            canvas.create_rectangle(*box, fill=color if name in PATTERNS[char] else '#29313f',
                                    outline='', tags=('cell_%d' % index, 'segment'))
