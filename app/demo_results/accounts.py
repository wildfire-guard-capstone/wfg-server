"""(김지오 전용 · 임시) 로그인 계정 — DB(users 표)를 쓰기 전까지 JSON 파일 하나로 둔다.

파일 형식::

    {"accounts": [{"login_id": "cmd01", "password_hash": "pbkdf2_sha256$...",
                   "name": "…", "organization": "…", "role": "COMMANDER", "disabled": false}]}

``role``은 ``app.db.models.enums.UserRole`` 멤버 이름. 계정 관리(비밀번호는 화면에 묻는다)::

    python -m app.demo_results.accounts add  --file accounts.json --login cmd01 --role COMMANDER \
        --name 홍길동 --org 의성군
    python -m app.demo_results.accounts list --file accounts.json
    python -m app.demo_results.accounts remove --file accounts.json --login cmd01

로그인 실패 5회면 그 아이디를 잠근다(DB 설계 ``users.failed_login_count``와 같은 기준).
잠금은 서버 메모리에만 있어 재시작하면 풀린다.
"""

import argparse
import getpass
import json
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from app.db.models.enums import UserRole
from app.demo_results.security import hash_password, verify_password

MAX_FAILED_LOGINS = 5


@dataclass(frozen=True)
class Account:
    login_id: str
    name: str
    organization: str
    role: UserRole


class AccountLocked(Exception):
    pass


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("accounts", [])


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text(
        json.dumps({"accounts": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


class AccountStore:
    def __init__(self, path: Path):
        self.path = path
        self._failed: dict[str, int] = {}
        self._lock = threading.Lock()

    def _rows(self) -> dict[str, dict]:
        # 아이디는 대소문자를 가리지 않는다(DB 설계 uq_users_login_id_lower).
        return {r["login_id"].lower(): r for r in _read(self.path) if not r.get("disabled")}

    def get(self, login_id: str) -> Account | None:
        row = self._rows().get(login_id.lower())
        return _account(row) if row else None

    def authenticate(self, login_id: str, password: str) -> Account | None:
        key = login_id.lower()
        with self._lock:
            if self._failed.get(key, 0) >= MAX_FAILED_LOGINS:
                raise AccountLocked(login_id)
        row = self._rows().get(key)
        ok = row is not None and verify_password(password, row["password_hash"])
        with self._lock:
            if ok:
                self._failed.pop(key, None)
                return _account(row)
            self._failed[key] = self._failed.get(key, 0) + 1
            return None


def _account(row: dict) -> Account:
    return Account(
        login_id=row["login_id"],
        name=row["name"],
        organization=row["organization"],
        role=UserRole[row["role"]],
    )


def add_account(
    path: Path, login_id: str, password: str, name: str, organization: str, role: UserRole
) -> None:
    rows = [r for r in _read(path) if r["login_id"].lower() != login_id.lower()]
    rows.append(
        {
            "login_id": login_id,
            "password_hash": hash_password(password),
            "name": name,
            "organization": organization,
            "role": role.name,
            "disabled": False,
        }
    )
    _write(path, rows)


def remove_account(path: Path, login_id: str) -> bool:
    rows = _read(path)
    kept = [r for r in rows if r["login_id"].lower() != login_id.lower()]
    _write(path, kept)
    return len(kept) != len(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.demo_results.accounts")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_add = sub.add_parser("add", help="계정 추가(같은 아이디가 있으면 바꿈)")
    p_add.add_argument("--file", type=Path, required=True)
    p_add.add_argument("--login", required=True)
    p_add.add_argument("--role", required=True, choices=[r.name for r in UserRole])
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--org", required=True)
    p_add.add_argument(
        "--password-stdin", action="store_true", help="비밀번호를 표준 입력 첫 줄에서 읽음"
    )
    p_list = sub.add_parser("list", help="계정 목록(비밀번호 해시는 안 보임)")
    p_list.add_argument("--file", type=Path, required=True)
    p_rm = sub.add_parser("remove", help="계정 삭제")
    p_rm.add_argument("--file", type=Path, required=True)
    p_rm.add_argument("--login", required=True)
    a = ap.parse_args(argv)

    if a.cmd == "add":
        if a.password_stdin:
            password = sys.stdin.readline().rstrip("\r\n")
        else:
            password = getpass.getpass("비밀번호: ")
            if password != getpass.getpass("비밀번호 확인: "):
                print("비밀번호가 다릅니다.", file=sys.stderr)
                return 1
        if len(password) < 8:
            print("비밀번호는 8자 이상이어야 합니다.", file=sys.stderr)
            return 1
        add_account(a.file, a.login, password, a.name, a.org, UserRole[a.role])
        print(f"추가: {a.login} ({UserRole[a.role].value})")
    elif a.cmd == "list":
        for r in _read(a.file):
            flag = " (사용 중지)" if r.get("disabled") else ""
            role = UserRole[r["role"]].value
            print(f"{r['login_id']}\t{role}\t{r['name']}\t{r['organization']}{flag}")
    elif a.cmd == "remove":
        print("삭제함" if remove_account(a.file, a.login) else "그런 아이디가 없습니다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
