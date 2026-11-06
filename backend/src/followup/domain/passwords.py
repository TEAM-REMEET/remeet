"""비밀번호 해시. 비밀번호 원문은 어디에도 저장하지 않고, 이 해시만 Person.password_hash에 저장한다.

표준 라이브러리 scrypt를 쓴다 (사람마다 다른 무작위 salt → 같은 비밀번호라도 해시가 다름).
저장 형식: scrypt$N$r$p$salt(hex)$hash(hex) — 설정값을 같이 저장해서 나중에 강도를 올려도 예전 해시를 검사할 수 있다.
"""

import hashlib
import hmac
import secrets

N, R, P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P)
    return f"scrypt${N}${R}${P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    try:
        algo, n, r, p, salt, digest = (stored or "").split("$")
        if algo != "scrypt":
            return False
        calc = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p))
    except ValueError:
        return False
    return hmac.compare_digest(calc, bytes.fromhex(digest))  # 비교 시간이 일정해서 한 글자씩 맞혀 보는 공격을 막음
