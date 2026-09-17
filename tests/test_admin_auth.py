# -*- coding: utf-8 -*-
"""어드민 인증(HTTP Basic) 자동 테스트.

fail-closed 정책(server/admin.py 의 _admin_auth_dependency)을 고정한다:
  - ADMIN_PASSWORD 미설정 → /admin/* 503 (무인증 허용 아님)
  - ALLOW_OPEN_ADMIN=1 이면 ADMIN_PASSWORD 없이도 무인증 허용(로컬 개발 옵트인)
  - 올바른 비밀번호 → 200 / 틀린 비밀번호 → 401 / 사용자명 오류 → 401
  - 한글 비밀번호도 인코딩 오류 없이(TypeError→500 이 아니라) 정상 동작
  - "/" · "/shop" 은 어드민 인증 설정과 무관하게 그대로 열린다
"""
import base64

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.domain import load_domain
from server.repo import Repo


class FakePipelineWithRepo:
    """통화 API 는 쓰지 않고 어드민·쇼핑몰 라우터 장착에 필요한 repo 만 들고 있는 가짜 파이프라인.
    Repo(server.repo) 를 그대로 써야 ShopRepo 가 기대하는 _lock 등 속성이 갖춰진다."""

    def __init__(self, db_path):
        self.repo = Repo(db_path)

    def start_call(self, phone=None):
        return "abc", "안녕하세요", None

    def end_call(self, call_id):
        pass

    def turn(self, call_id, text):
        raise NotImplementedError


@pytest.fixture(autouse=True)
def _clean_admin_env(monkeypatch):
    # 셸 환경에 ADMIN_PASSWORD/ALLOW_OPEN_ADMIN 이 남아 있으면 테스트가 실제 환경에 따라
    # 결과가 흔들린다. 매 테스트를 "둘 다 비어 있는" 상태에서 시작하고, 각 테스트가
    # 필요한 값만 monkeypatch.setenv 로 켠다.
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("ALLOW_OPEN_ADMIN", raising=False)


def _client(modumall_dir):
    domain = load_domain(modumall_dir)
    return TestClient(create_app(FakePipelineWithRepo(domain.db_path), domain))


def _basic(username: str, password: str) -> dict:
    raw = f"{username}:{password}".encode("utf-8")
    return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}


def test_unset_password_returns_503(modumall_dir):
    c = _client(modumall_dir)
    r = c.get("/admin")
    assert r.status_code == 503
    assert "어드민 비밀번호가 설정되지 않았습니다" in r.text


def test_empty_password_returns_503(modumall_dir, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "")
    c = _client(modumall_dir)
    assert c.get("/admin").status_code == 503


def test_allow_open_admin_opts_in_without_password(modumall_dir, monkeypatch):
    monkeypatch.setenv("ALLOW_OPEN_ADMIN", "1")
    c = _client(modumall_dir)
    r = c.get("/admin")
    assert r.status_code == 200
    assert "<h1>요약</h1>" in r.text


def test_correct_password_returns_200(modumall_dir, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "hunter2")
    c = _client(modumall_dir)
    r = c.get("/admin", headers=_basic("admin", "hunter2"))
    assert r.status_code == 200
    assert "<h1>요약</h1>" in r.text


def test_wrong_password_returns_401(modumall_dir, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "hunter2")
    c = _client(modumall_dir)
    r = c.get("/admin", headers=_basic("admin", "wrong"))
    assert r.status_code == 401


def test_wrong_username_returns_401(modumall_dir, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "hunter2")
    c = _client(modumall_dir)
    r = c.get("/admin", headers=_basic("root", "hunter2"))
    assert r.status_code == 401


def test_korean_password_success(modumall_dir, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "가나다라비밀")
    c = _client(modumall_dir)
    r = c.get("/admin", headers=_basic("admin", "가나다라비밀"))
    assert r.status_code == 200


def test_korean_password_failure_is_401_not_500(modumall_dir, monkeypatch):
    # 실측 버그: secrets.compare_digest 가 non-ASCII str 을 그대로 비교하면 TypeError 로
    # 새어 나가 500 이 됐다. UTF-8 인코딩 후 비교하면 인증 실패는 언제나 401 이어야 한다.
    monkeypatch.setenv("ADMIN_PASSWORD", "가나다라비밀")
    c = _client(modumall_dir)
    r = c.get("/admin", headers=_basic("admin", "틀린비밀번호"))
    assert r.status_code == 401


def test_no_credentials_returns_401_when_password_set(modumall_dir, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "hunter2")
    c = _client(modumall_dir)
    r = c.get("/admin")
    assert r.status_code == 401


@pytest.mark.parametrize("scenario", ["unset", "wrong_password", "allow_open"])
def test_call_screen_and_shop_unaffected(modumall_dir, monkeypatch, scenario):
    if scenario == "wrong_password":
        monkeypatch.setenv("ADMIN_PASSWORD", "hunter2")
    elif scenario == "allow_open":
        monkeypatch.setenv("ALLOW_OPEN_ADMIN", "1")
    c = _client(modumall_dir)
    assert c.get("/").status_code == 200
    assert c.get("/shop").status_code == 200
