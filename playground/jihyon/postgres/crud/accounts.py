from __future__ import annotations
import base64
import hashlib
import hmac
import os
from db import transaction
from .common import (ValidationError, PermissionDenied, NotFoundError, required,
                     account, is_admin, get_one)

ITERATIONS=600_000

def _hash_password(password: str) -> str:
    if not isinstance(password,str) or len(password)<12:
        raise ValidationError('비밀번호는 12자 이상이어야 합니다.')
    salt=os.urandom(16)
    digest=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,ITERATIONS)
    return 'pbkdf2_sha256${}${}${}'.format(ITERATIONS,base64.b64encode(salt).decode(),base64.b64encode(digest).decode())


def _verify_password(password: str, stored: str | None) -> bool:
    if not stored: return False
    try:
        algorithm, count, salt, expected=stored.split('$')
        if algorithm!='pbkdf2_sha256': return False
        rounds=int(count)
        if not 100_000 <= rounds <= 1_000_000: return False
        found=hashlib.pbkdf2_hmac('sha256',password.encode(),base64.b64decode(salt),rounds)
        return hmac.compare_digest(found,base64.b64decode(expected))
    except (ValueError,TypeError):
        return False


def create_account(*, actor_id: str,user_id: str,email: str,password: str,role: str='USER') -> dict:
    if role not in {'USER','ADMIN'}: raise ValidationError('잘못된 계정 권한입니다.')
    secret=_hash_password(password)
    with transaction() as cur:
        if not is_admin(cur,actor_id): raise PermissionDenied('관리자만 계정을 생성할 수 있습니다.')
        cur.execute('''INSERT INTO accounts(user_id,email,password_hash,role)
                       VALUES (%s,%s,%s,%s) RETURNING user_id,email,role,created_at,last_login_at''',
                    (required(user_id,'사용자 ID'),required(email,'이메일'),secret,role))
        return cur.fetchone()


def get_account(*,actor_id: str,user_id: str) -> dict:
    with transaction() as cur:
        if actor_id!=user_id and not is_admin(cur,actor_id):
            raise PermissionDenied('본인 또는 관리자만 조회할 수 있습니다.')
        return get_one(cur,'''SELECT user_id,email,role,created_at,last_login_at
                              FROM accounts WHERE user_id=%s''',(user_id,))


def list_accounts(*,actor_id: str) -> list[dict]:
    with transaction() as cur:
        if not is_admin(cur,actor_id): raise PermissionDenied('관리자만 목록을 볼 수 있습니다.')
        cur.execute('SELECT user_id,email,role,created_at,last_login_at FROM accounts ORDER BY user_id')
        return cur.fetchall()


def update_account(*,actor_id: str,user_id: str,email: str | None = None,
                   password: str | None = None,role: str | None = None) -> dict:
    if all(v is None for v in (email,password,role)):
        raise ValidationError('수정할 필드가 없습니다.')
    if role is not None and role not in {'USER','ADMIN'}: raise ValidationError('잘못된 계정 권한입니다.')
    secret=_hash_password(password) if password is not None else None
    with transaction() as cur:
        admin=is_admin(cur,actor_id)
        if not admin and (actor_id!=user_id or role is not None):
            raise PermissionDenied('수정 권한이 없습니다.')
        cur.execute('''UPDATE accounts SET email=COALESCE(%s,email),
                       password_hash=COALESCE(%s,password_hash),role=COALESCE(%s,role)
                       WHERE user_id=%s RETURNING user_id,email,role,created_at,last_login_at''',
                    (required(email,'이메일') if email is not None else None,secret,role,user_id))
        row=cur.fetchone()
        if row is None:
            raise NotFoundError('계정을 찾지 못했습니다.')

        from .common import invalidate

        invalidate('_base_meetings', 'base_folders')
        return row


def delete_account(*,actor_id: str,user_id: str) -> None:
    """Prototype physical delete; production should prefer active/disabled state."""
    with transaction() as cur:
        if not is_admin(cur,actor_id): raise PermissionDenied('관리자만 계정을 삭제할 수 있습니다.')
        if actor_id==user_id: raise ValidationError('현재 로그인한 관리자 계정은 삭제할 수 없습니다.')
        cur.execute('DELETE FROM accounts WHERE user_id=%s RETURNING user_id',(user_id,))
        if cur.fetchone() is None: raise NotFoundError('계정을 찾지 못했습니다.')


def authenticate(*,email: str,password: str) -> dict | None:
    with transaction() as cur:
        cur.execute('SELECT user_id,email,role,password_hash FROM accounts WHERE email=%s',(email,))
        row=cur.fetchone()
        if row is None or not _verify_password(password,row['password_hash']): return None
        cur.execute('UPDATE accounts SET last_login_at=now() WHERE user_id=%s',(row['user_id'],))
        return {'user_id':row['user_id'],'email':row['email'],'role':row['role']}
