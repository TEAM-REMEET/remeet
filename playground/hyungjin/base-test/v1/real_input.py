"""Prepare a timestamped real-meeting transcript for the baseline pipeline."""
import os
import re
from pathlib import Path

import numpy as np

from experiment import REPO
HEADER = re.compile(r'(?m)^(\d{1,2}:\d{2}(?::\d{2})?)\s+(참석자\s*\d+)\s*$')


def parse_transcript(text):
    markers = list(HEADER.finditer(text))
    if not markers or text[:markers[0].start()].strip():
        raise ValueError('첫 발화 앞에 처리되지 않은 내용이 있거나 발화 표식이 없습니다.')
    utterances = []
    for i, marker in enumerate(markers):
        end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        body = text[marker.end():end].strip()
        if not body:
            raise ValueError(f'빈 발화가 있습니다: {i}')
        utterances.append({'utterance_index': i, 'text': body, 'timestamp': marker.group(1), 'speaker': marker.group(2)})
    return utterances


def boundary_segments(utterances):
    # PR #35: 200/100 발화 청크, 발화 임베딩 글자 수 가중 풀링, block size 2.
    os.environ.setdefault('HF_HUB_OFFLINE', '1')
    import torch
    from sentence_transformers import SentenceTransformer
    torch.set_num_threads(min(8, torch.get_num_threads()))
    texts = [u['text'] for u in utterances]
    lengths = np.asarray([len(t) for t in texts])
    parts, start = [], 0
    while start < len(texts):
        end = start + 1
        while end < len(texts) and len('\n'.join(texts[start:end + 1])) <= 200:
            end += 1
        parts.append((start, end))
        if end == len(texts):
            break
        nxt = end
        while nxt - 1 > start and len('\n'.join(texts[nxt - 1:end])) <= 100:
            nxt -= 1
        start = nxt
    print(f'임베딩 준비: 발화 {len(texts)}개, 탐지용 청크 {len(parts)}개', flush=True)
    model = SentenceTransformer('BAAI/bge-m3', device='cpu', local_files_only=True)
    embeddings = model.encode(texts, batch_size=16, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=True).astype(np.float32)
    def norm(a):
        magnitude = np.linalg.norm(a, axis=-1, keepdims=True)
        if np.any(magnitude == 0):
            raise ValueError('임베딩 길이가 0입니다.')
        return a / magnitude
    vectors = []
    for a, b in parts:
        indices = np.arange(a, b)
        content = indices[lengths[indices] > 4]
        indices = content if len(content) else indices
        vectors.append((embeddings[indices] * lengths[indices, None]).sum(axis=0))
    vectors = norm(np.asarray(vectors))
    sims = np.array([float(norm(vectors[max(0, g-1):g+1].mean(axis=0)) @ norm(vectors[g+1:g+3].mean(axis=0))) for g in range(len(parts)-1)])
    depths = []
    for i in range(len(sims)):
        j, left = i, sims[i]
        while j > 0 and sims[j-1] >= left:
            j -= 1; left = sims[j]
        j, right = i, sims[i]
        while j < len(sims)-1 and sims[j+1] >= right:
            j += 1; right = sims[j]
        depths.append(float(left + right - 2*sims[i]))
    threshold = float(np.mean(depths)) if depths else 0.0
    candidates = []
    for g, depth in enumerate(depths):
        if depth > 0 and depth >= threshold:
            candidates.append({'boundary_position': (parts[g+1][0] + parts[g][1]) // 2,
                               'gap_index': g, 'similarity': round(float(sims[g]), 4), 'depth_score': round(depth, 4)})
    groups = []
    for candidate in sorted(candidates, key=lambda x: x['boundary_position']):
        pos = candidate['boundary_position']
        if groups and lengths[groups[-1][-1]['boundary_position']:pos].sum() <= 400 and lengths[groups[-1][0]['boundary_position']:pos].sum() <= 800:
            groups[-1].append(candidate)
        else:
            groups.append([candidate])
    boundaries = [max(group, key=lambda x: x['depth_score'])['boundary_position'] for group in groups]
    starts = [0] + [b for b in boundaries if b > 0]
    if starts != sorted(set(starts)):
        raise ValueError('경계 발화 번호가 중복되거나 순서가 잘못됐습니다.')
    segments = [utterances[a:b] for a,b in zip(starts, starts[1:] + [len(utterances)])]
    detection = {'embedding_model':'BAAI/bge-m3', 'embedding_mode':'utterance_pooled', 'chunk_size':200,
                 'chunk_overlap':100, 'block_size':2, 'filler_max_chars':4, 'depth_std_k':0.0,
                 'depth_threshold':threshold, 'merge_gap_chars':400, 'merge_max_span_chars':800,
                 'segment_overlap_chars':0, 'num_candidates':len(candidates), 'num_segments':len(segments)}
    return segments, detection, candidates


def prepare_real(input_path, meeting_id):
    input_path = Path(input_path)
    utterances = parse_transcript(input_path.read_text(encoding='utf-8-sig'))
    segments, detection, candidates = boundary_segments(utterances)
    chunks = []
    for segment_id, segment in enumerate(segments):
        current = []
        for u in segment:
            text = u['text']
            if current and sum(len(v['텍스트']) for v in current) + len(current) + len(text) > 1000:
                chunks.append({'청크ID':len(chunks)+1, 'segment_id':segment_id, '발화':current})
                current = []
            current.append({'번호':u['utterance_index'], '시각':u['timestamp'], '화자':u['speaker'], '텍스트':text})
        if current:
            chunks.append({'청크ID':len(chunks)+1, 'segment_id':segment_id, '발화':current})
    if [u['번호'] for c in chunks for u in c['발화']] != list(range(len(utterances))):
        raise ValueError('발화가 누락되거나 중복되었습니다.')
    try:
        source_path = input_path.resolve().relative_to(REPO.resolve()).as_posix()
    except ValueError:
        source_path = f'외부 파일/{input_path.name}'
    metadata = {'회의 대주제':input_path.stem, '참여자':', '.join(sorted({u['speaker'] for u in utterances})),
                '일시':None, '원본 문서':source_path}
    data = {'meeting_id':meeting_id, 'metadata':metadata, 'detection':detection}
    print(f"준비 완료: 발화 {len(utterances)}개, 경계 {len(candidates)}개, 구간 {len(segments)}개, 추출 청크 {len(chunks)}개", flush=True)
    return data, chunks, candidates
