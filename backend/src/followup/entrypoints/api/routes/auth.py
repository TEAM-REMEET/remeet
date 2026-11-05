"""로그인. 이메일 · 비밀번호를 확인하고 출입증(토큰)을 준다. 이후 요청은 Authorization: Bearer <토큰> 헤더로 보낸다."""

from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from followup import settings
from followup.adapters.neo4j import people
from followup.domain.passwords import verify_password
from followup.domain.tokens import make_token, read_token

router = APIRouter(prefix="/api/auth", tags=["auth"])

_WRONG = "이메일 또는 비밀번호가 맞지 않아요."  # 이메일이 없는지 비밀번호가 틀렸는지 알려 주지 않음 (계정 찾기 방지)


class LoginBody(BaseModel):
    email: str
    password: str


class Me(BaseModel):
    user_id: str
    name: str
    email: str
    permission: Literal["admin", "member"]


class LoginResult(BaseModel):
    token: str
    user: Me


def current_user(authorization: str = Header(default="")) -> dict:
    """요청 헤더의 토큰 → 로그인한 사람. 없거나 틀리거나 만료면 401."""
    claims = read_token(authorization.removeprefix("Bearer ").strip(), settings.AUTH_SECRET)
    if claims is None:
        raise HTTPException(status_code=401, detail="로그인이 필요해요.")
    return claims


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user.get("permission") != "admin":
        raise HTTPException(status_code=403, detail="관리자만 할 수 있어요.")
    return user


@router.post("/login", response_model=LoginResult)
def login(body: LoginBody):
    account = people.find_login(body.email.strip().lower())
    if account is None or not verify_password(body.password, account["password_hash"]):
        raise HTTPException(status_code=401, detail=_WRONG)
    if not account["is_active"]:
        raise HTTPException(status_code=403, detail="비활성화된 계정이에요. 관리자에게 문의해 주세요.")
    user = {k: account[k] for k in ("user_id", "name", "email", "permission")}
    token = make_token(user, settings.AUTH_SECRET, settings.AUTH_TOKEN_DAYS * 86400)
    return {"token": token, "user": user}


@router.get("/me", response_model=Me)
def me(user: dict = Depends(current_user)):
    """새로고침 후 쿠키의 토큰으로 로그인 상태를 되살릴 때 쓴다."""
    return user
