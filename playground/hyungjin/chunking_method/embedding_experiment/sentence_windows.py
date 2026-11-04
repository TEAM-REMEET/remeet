"""Punctuation-aligned windows with source offsets preserved."""
from bisect import bisect_left, bisect_right
import re

# shortcut: punctuation rules do not disambiguate abbreviations; use a language-aware
# sentence splitter if abbreviation errors materially affect this corpus.
TERMINATOR = re.compile(r'''[.!?。！？…]+["'”’」』）)\]]*(?=\s|$)''')


def sentence_spans(source, start, end):
    """Keep fragments without terminal punctuation attached until the next terminator."""
    cuts = [start]
    for match in TERMINATOR.finditer(source, start, end):
        cut = match.end()
        while cut < end and source[cut].isspace():
            cut += 1
        if cut > cuts[-1]:
            cuts.append(cut)
    if cuts[-1] != end:
        cuts.append(end)
    return list(zip(cuts, cuts[1:]))


def nearest(values, target, minimum_index=0):
    i = max(minimum_index, bisect_left(values, target))
    options = values[max(minimum_index, i-1):min(len(values), i+1)]
    return min(options, key=lambda x: (abs(x-target), x))


def build_sentence_windows(blocks, source, window_chars=300, overlap_chars=200):
    if not blocks or not 0 <= overlap_chars < window_chars:
        raise ValueError("Invalid window settings or empty transcript")
    start, end = blocks[0]['start_char'], blocks[-1]['end_char']
    spans = sentence_spans(source, start, end)
    starts, ends = [a for a,b in spans], [b for a,b in spans]
    windows = []
    for target in range(start, end, window_chars-overlap_chars):
        a = nearest(starts, target)
        minimum_end = 0
        if windows:
            previous = windows[-1]
            a = min(a, starts[bisect_right(starts, previous['end_char'])-1])
            if a <= previous['start_char']:
                continue
            minimum_end = max(bisect_right(ends, a), bisect_left(ends, previous['end_char']))
        b = nearest(ends, a+window_chars, minimum_end)
        windows.append({'window_index':len(windows), 'start_char':a, 'end_char':b,
                        'char_count':b-a, 'text':source[a:b]})
        if b == end:
            break
    assert windows[0]['start_char'] == start and windows[-1]['end_char'] == end
    assert all(w['start_char'] in starts and w['end_char'] in ends and w['text'].strip()
               and w['text'] == source[w['start_char']:w['end_char']] for w in windows)
    assert all(a['start_char'] < b['start_char'] <= a['end_char'] <= b['end_char']
               for a,b in zip(windows,windows[1:])), 'Unexpected gap/nonmonotonic windows'
    return windows


def self_check():
    text = '가격은 3.14예요. "좋아요!"\n\n아직 미완성\n\n이어서 설명해요？ 끝'
    spans = sentence_spans(text,0,len(text))
    assert len(spans)==4 and '3.14' in text[slice(*spans[0])]
    assert '아직 미완성' in text[slice(*spans[2])] and '이어서' in text[slice(*spans[2])]
    assert ''.join(text[a:b] for a,b in spans)==text
    assert len(sentence_spans('마침표 없는 발화',0,10))==1
    for text in ['긴 문장입니다.', 'A. B. C. D. E.', '아주긴문장'*80+'. 다음. 마지막']:
        blocks=[{'start_char':0,'end_char':len(text)}]
        windows=build_sentence_windows(blocks,text,30,20)
        assert all(w['text']==text[w['start_char']:w['end_char']] for w in windows)
        assert all(a['start_char'] < b['start_char'] <= a['end_char'] <= b['end_char']
                   for a,b in zip(windows,windows[1:]))


if __name__=='__main__':
    self_check()
    print('PASS: decimals, quotes, incomplete fragments, missing punctuation, long sentences, exact source offsets')
