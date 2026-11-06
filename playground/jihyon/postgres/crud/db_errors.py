# Translate database exceptions for display; preserve original errors in logs.
import psycopg


def user_message(exc: Exception) -> str:
    if isinstance(exc, psycopg.errors.UniqueViolation):
        return '이미 등록된 데이터입니다.'
    if isinstance(exc, psycopg.errors.ForeignKeyViolation):
        return '연결된 데이터가 없거나 다른 데이터에서 사용 중입니다.'
    if isinstance(exc, psycopg.errors.CheckViolation):
        return '입력값이 데이터베이스 규칙에 맞지 않습니다.'
    if isinstance(exc, psycopg.errors.InsufficientPrivilege):
        return '이 작업에 대한 접근 권한이 없습니다.'
    if isinstance(exc, (psycopg.OperationalError, psycopg.InterfaceError)):
        return '데이터베이스에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.'
    return '처리 중 오류가 발생했습니다. 관리자에게 문의해 주세요.'
