# Apply numbered SQL migrations without dropping existing tables.
# Requires DATABASE_URL to be explicitly set.
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import psycopg

ROOT = Path(__file__).resolve().parent

def main():
    url = os.environ.get('DATABASE_URL')
    if not url:
        raise SystemExit('DATABASE_URL 환경변수를 먼저 설정하세요 (자동 기본값 사용 금지).')
    files = sorted((ROOT / 'migrations').glob('[0-9][0-9][0-9]_*.sql'))
    with psycopg.connect(url) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute('''CREATE TABLE IF NOT EXISTS schema_migrations (
                  filename text PRIMARY KEY, checksum text NOT NULL,
                  applied_at timestamptz NOT NULL DEFAULT now())''')
                cur.execute('SELECT pg_advisory_xact_lock(77812345)')
                for f in files:
                    content=f.read_text(encoding='utf-8')
                    checksum=hashlib.sha256(content.encode('utf-8')).hexdigest()
                    cur.execute('SELECT checksum FROM schema_migrations WHERE filename=%s',(f.name,))
                    row=cur.fetchone()
                    if row:
                        if row[0]!=checksum:
                            raise RuntimeError(f'{f.name}: 이미 적용한 마이그레이션의 내용이 변경됐습니다.')
                        print(f'SKIP {f.name}')
                        continue
                    # psycopg parameterless execute accepts multi statements in simple protocol
                    cur.execute(content)
                    cur.execute('INSERT INTO schema_migrations(filename,checksum) VALUES(%s,%s)',
                                (f.name,checksum))
                    print(f'APPLY {f.name}')
    print('마이그레이션 완료')
if __name__ == '__main__': main()
