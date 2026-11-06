"""확정된 액션 아이템을 담당자에게 메일로 알린다 (SMTP)."""

import argparse
import json
from pathlib import Path

from dotenv import dotenv_values

from integrations import mail_message, send_mail

ENV_FILE = Path(__file__).with_name(".env")


def settings():
    values = dotenv_values(ENV_FILE)
    required = ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "MAIL_FROM")
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise ValueError(".env 설정이 필요합니다: " + ", ".join(missing))
    return values


def main():
    parser = argparse.ArgumentParser(description="확정된 액션 아이템 메일 알림")
    parser.add_argument("command", choices=["preview", "send"], help="preview: 보내지 않고 출력, send: 실제 발송")
    parser.add_argument("input", help="액션 아이템 추천 결과 JSON 경로")
    parser.add_argument("--to", help="받는 주소 (.env의 MAIL_TEST_TO보다 우선)")
    parser.add_argument("--issue-url", help="본문에 넣을 Jira 이슈 주소")
    args = parser.parse_args()

    config = settings()
    to = args.to or config.get("MAIL_TEST_TO")
    with open(args.input, encoding="utf-8") as file:
        message = mail_message(json.load(file), config["MAIL_FROM"], to, args.issue_url)
    if args.command == "preview":
        for key in ("Subject", "From", "To"):
            print(f"{key}: {message[key]}")
        print("\n" + message.get_content())
        return
    send_mail(config, message)
    print(f"발송 완료: {to}")


if __name__ == "__main__":
    main()
