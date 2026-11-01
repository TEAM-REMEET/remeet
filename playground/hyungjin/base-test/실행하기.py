"""Single entry point for full runs, saved action review, and final assembly."""
import argparse
from pathlib import Path

from experiment import Experiment, HERE, REPO, prepare, save
from final_assembly import load_inputs, read, run as assemble


MEETINGS = ('TS3003a', 'TS3003b', 'TS3003c', 'TS3003d')


def saved_candidates(meeting_id, source):
    source = Path(source)
    old_input = read(source / '00_입력.json')
    _, chunks = prepare(meeting_id)
    if old_input['meeting_id'] != meeting_id or old_input['chunks'] != chunks:
        raise ValueError('기존 실행 입력이 현재 원문과 일치하지 않습니다.')
    actions = [read(source / '03_액션' / f"{c['청크ID']:03d}.json") for c in chunks]
    if [a.get('chunk_id') for a in actions] != [c['청크ID'] for c in chunks]:
        raise ValueError('기존 액션 후보의 청크 순서가 일치하지 않습니다.')
    return read(source / '02_안건목록.json'), actions


def default_source(meeting):
    return HERE / 'test_output' / f'{meeting}_test'


def default_action(summary):
    return summary if (summary / '11_액션수정.json').exists() else summary / '03_액션'


def validate_inputs(mode, meeting, summary=None, action=None):
    if mode == 'full':
        prepare(meeting)
    elif mode == 'actions':
        saved_candidates(meeting, summary)
    else:
        load_inputs(meeting, summary, action)


def execute(mode, meeting, summary=None, action=None):
    if mode == 'final':
        return assemble(meeting, summary, action)
    if mode == 'actions':
        agendas, actions = saved_candidates(meeting, summary)
    exp = Experiment(meeting)
    try:
        if mode == 'full':
            exp.run_chunks()
            exp.merge_and_review()
            exp.review_actions()
            return assemble(meeting, exp.output, exp.output, exp)
        exp.agendas, exp.actions = agendas, actions
        try:
            source_ref = Path(summary).resolve().relative_to(REPO.resolve()).as_posix()
        except ValueError:
            source_ref = f'외부 폴더/{Path(summary).name}'
        save(exp.output / '기존_액션_출처.json', {'source_run': source_ref, 'mode': 'existing_candidates_with_full_transcript', 'chunk_extraction_repeated': False})
        save(exp.output / '03_기존액션후보.json', actions)
        save(exp.output / '02_기존안건목록.json', agendas)
        exp.review_actions()
        print(f'완료: {meeting} / {exp.output}', flush=True)
        return exp.output
    except Exception as exc:
        exp.record('failed', f'{mode}: {type(exc).__name__}')
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description='회의록 베이스 실험 실행')
    parser.add_argument('mode', choices=('full', 'actions', 'final'), help='전체 실행 / 기존 액션 검증 / 최종 회의록 조립')
    parser.add_argument('--meetings', nargs='+', choices=(*MEETINGS, 'all'), required=True, help='회의 ID 또는 all')
    parser.add_argument('--summary', type=Path, help='기존 요약 결과 폴더 (회의 한 개일 때)')
    parser.add_argument('--action', type=Path, help='기존 액션 검증 결과 폴더 (final, 회의 한 개일 때)')
    parser.add_argument('--check', action='store_true', help='입력만 확인하고 모델 호출은 하지 않음')
    args = parser.parse_args(argv)
    meetings = list(MEETINGS) if args.meetings == ['all'] else args.meetings
    if 'all' in meetings or len(set(meetings)) != len(meetings):
        parser.error('all은 단독으로 사용하고, 회의 ID는 중복하지 마세요.')
    if (args.summary or args.action) and len(meetings) != 1:
        parser.error('별도 결과 폴더는 회의 하나를 선택했을 때만 지정할 수 있습니다.')
    if args.mode == 'full' and (args.summary or args.action):
        parser.error('full 실행은 기존 결과 폴더를 사용하지 않습니다.')
    if args.mode == 'actions' and args.action:
        parser.error('--action은 final 모드에서만 사용합니다.')
    inputs = []
    for meeting in meetings:
        summary = (args.summary or default_source(meeting)) if args.mode != 'full' else None
        action = (args.action or default_action(summary)) if args.mode == 'final' else None
        validate_inputs(args.mode, meeting, summary, action)
        inputs.append((meeting, summary, action))
        print(f'입력 확인: {meeting}', flush=True)
    if args.check:
        return 0
    failed = []
    for meeting, summary, action in inputs:
        try:
            execute(args.mode, meeting, summary, action)
        except Exception as exc:
            failed.append(meeting)
            print(f'실패: {meeting} / {type(exc).__name__}: {exc}', flush=True)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
